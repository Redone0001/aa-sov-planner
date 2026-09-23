"""Creation-time public ESI sovereignty snapshots; no character tokens required."""

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

from . import __version__
from .models import PlannedSystem


@shared_task(
    autoretry_for=(requests.RequestException, ValueError),
    retry_backoff=30,
    retry_kwargs={"max_retries": 5},
)
def snapshot_ownership(project_id, solar_system_ids):
    pending = PlannedSystem.objects.filter(
        project_id=project_id, solar_system_id__in=solar_system_ids, owner_observed_at__isnull=True
    )
    wanted = set(pending.values_list("solar_system_id", flat=True))
    if not wanted:
        return
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
    if set(owners) != wanted:
        raise ValueError("ESI sovereignty response omitted requested systems; no snapshot saved.")
    ids = sorted({owner_id for _, owner_id in owners.values() if owner_id})
    names = {}
    for start in range(0, len(ids), 1000):
        response = requests.post(
            "https://esi.evetech.net/universe/names",
            headers=headers,
            json=ids[start : start + 1000],
            timeout=(5, 30),
        )
        response.raise_for_status()
        names.update({r["id"]: r["name"] for r in response.json()})
    if set(ids) - names.keys():
        raise ValueError("ESI omitted owner names; no snapshot saved.")
    for solar_id, (kind, owner_id) in owners.items():
        pending.filter(solar_system_id=solar_id).update(
            owner_id=owner_id,
            owner_kind=kind,
            owner_name=names.get(owner_id, "Unclaimed"),
            owner_observed_at=observed,
        )
