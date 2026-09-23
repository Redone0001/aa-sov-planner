"""Public ESI ownership snapshots, with persistent queue and failure diagnostics."""

import logging

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from . import __version__
from .models import PlannedSystem

logger = logging.getLogger(__name__)


def pending_systems(project_id, solar_system_ids):
    return PlannedSystem.objects.filter(
        project_id=project_id, solar_system_id__in=solar_system_ids, owner_observed_at__isnull=True
    )


def queue_ownership_snapshot(project_id, solar_system_ids):
    pending = pending_systems(project_id, solar_system_ids)
    wanted = list(pending.values_list("solar_system_id", flat=True))
    if not wanted:
        return 0
    pending.update(owner_queued_at=timezone.now(), owner_error="")
    try:
        snapshot_ownership.delay(project_id, wanted)
    except Exception:
        pending.update(
            owner_error="Could not queue ownership lookup. Check the AA Celery broker connection and worker logs, then retry."
        )
        logger.exception("Unable to queue ownership lookup for project %s", project_id)
        raise
    return len(wanted)


@shared_task(
    autoretry_for=(requests.RequestException, ValueError),
    retry_backoff=30,
    retry_kwargs={"max_retries": 5},
)
def snapshot_ownership(project_id, solar_system_ids):
    return capture_ownership(project_id, solar_system_ids)


def capture_ownership(project_id, solar_system_ids):
    """Capture missing snapshots. Callable directly to diagnose without Celery."""
    pending = pending_systems(project_id, solar_system_ids)
    wanted = set(pending.values_list("solar_system_id", flat=True))
    if not wanted:
        return 0
    pending.update(owner_attempted_at=timezone.now(), owner_error="")
    stage = "sovereignty lookup"
    try:
        headers = {
            "User-Agent": f"aa-sov-planner/{__version__} (https://github.com/Redone0001/aa-sov-planner; {getattr(settings, 'ESI_USER_CONTACT_EMAIL', '')})",
            "X-Compatibility-Date": "2026-08-18",
        }
        response = requests.get(
            "https://esi.evetech.net/sovereignty/systems", headers=headers, timeout=(5, 30)
        )
        response.raise_for_status()
        rows = response.json()["solar_systems"]
        observed = timezone.now()
        owners = {}
        for row in rows:
            pk = row["solar_system_id"]
            if pk not in wanted:
                continue
            claim = row["claim"]
            if claim.get("alliance"):
                owners[pk] = ("alliance", claim["alliance"]["alliance_id"])
            elif claim.get("faction"):
                owners[pk] = ("faction", claim["faction"]["faction_id"])
            elif claim.get("unclaimed"):
                owners[pk] = ("unclaimed", None)
        ids = sorted({owner_id for _, owner_id in owners.values() if owner_id})
        names = {}
        stage = "owner-name lookup"
        for start in range(0, len(ids), 1000):
            response = requests.post(
                "https://esi.evetech.net/universe/names",
                headers=headers,
                json=ids[start : start + 1000],
                timeout=(5, 30),
            )
            response.raise_for_status()
            names.update({r["id"]: r["name"] for r in response.json()})
        saved = 0
        for solar_id, (kind, owner_id) in owners.items():
            if owner_id is not None and owner_id not in names:
                continue
            saved += pending.filter(solar_system_id=solar_id).update(
                owner_id=owner_id,
                owner_kind=kind,
                owner_name=names.get(owner_id, "Unclaimed"),
                owner_observed_at=observed,
                owner_error="",
            )
        missing_systems = wanted - owners.keys()
        missing_names = set(ids) - names.keys()
        if missing_systems:
            raise ValueError(
                "ESI sovereignty response omitted these systems or their claim data. Available systems were saved; missing systems remain unknown."
            )
        if missing_names:
            raise ValueError(
                "ESI omitted owner names. Available snapshots were saved; retry the remaining systems."
            )
        return saved
    except (requests.RequestException, ValueError, KeyError, TypeError) as error:
        if isinstance(error, requests.RequestException):
            status = getattr(getattr(error, "response", None), "status_code", None)
            message = (
                f"ESI {stage} failed (HTTP {status})."
                if status
                else f"ESI {stage} failed ({type(error).__name__}). Check connectivity and retry."
            )
        elif isinstance(error, (KeyError, TypeError)):
            message = f"Unexpected ESI response during {stage}. Check the worker logs and retry."
        else:
            message = str(error)[:1000]
        pending.update(owner_error=message)
        logger.warning("Ownership lookup failed for project %s: %s", project_id, message)
        if isinstance(error, (KeyError, TypeError)):
            raise ValueError(message) from error
        raise
