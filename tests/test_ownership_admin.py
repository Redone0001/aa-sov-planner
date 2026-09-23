from io import StringIO
from unittest.mock import Mock, patch

import pytest
import requests
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.utils import timezone

from aasov.tasks import capture_ownership, queue_ownership_snapshot

pytestmark = pytest.mark.django_db


def test_admin_shows_ownership_snapshot_and_diagnostics(world, client, django_user_model):
    system = world.system("Owned system")
    system.owner_name = "Example Alliance"
    system.owner_id = 990001
    system.owner_kind = "alliance"
    system.owner_observed_at = timezone.now()
    system.save()
    failed = world.system("Failed system")
    failed.owner_error = "ESI sovereignty lookup failed (HTTP 503)."
    failed.owner_attempted_at = timezone.now()
    failed.save()
    user = django_user_model.objects.create_superuser("admin", "admin@example.invalid", "test")
    client.force_login(user)
    project_url = reverse("admin:aasov_project_change", args=[world.project.pk])
    page = client.get(project_url)
    assert page.status_code == 200
    html = page.content.decode()
    assert "Example Alliance" in html and "1 / 2 captured" in html
    assert "Snapshot time (UTC)" in html and "HTTP 503" in html
    page = client.get(reverse("admin:aasov_plannedsystem_changelist"))
    assert page.status_code == 200 and b"990001" in page.content
    detail = client.get(reverse("admin:aasov_plannedsystem_change", args=[failed.pk]))
    assert detail.status_code == 200 and b"HTTP 503" in detail.content
    assert b'name="_save"' not in detail.content
    assert (
        client.get(reverse("admin:aasov_plannedsystem_changelist"), {"snapshot": "failed"})
        .context["cl"]
        .result_count
        == 1
    )


def test_admin_action_queues_only_missing_and_requires_project_change(world, reader, client):
    system = world.system("Pending")
    reader.is_staff = True
    reader.save()
    client.force_login(reader)
    endpoint = reverse("admin:aasov_plannedsystem_changelist")
    assert client.get(endpoint).status_code == 200
    with patch("aasov.tasks.snapshot_ownership.delay") as task:
        client.post(
            endpoint, {"action": "capture_missing_ownership", "_selected_action": [system.pk]}
        )
        task.assert_not_called()
        reader.user_permissions.add(
            Permission.objects.get(content_type__app_label="aasov", codename="change_project")
        )
        result = client.post(
            endpoint, {"action": "capture_missing_ownership", "_selected_action": [system.pk]}
        )
        assert result.status_code == 302
        task.assert_called_once_with(world.project.pk, [system.solar_system_id])
    system.refresh_from_db()
    assert system.ownership_status == "Queued; waiting for worker"
    assert system.owner_queued_at


def test_queue_failure_is_visible_and_retry_clears_error(world):
    system = world.system("Pending")
    with (
        patch(
            "aasov.tasks.snapshot_ownership.delay",
            side_effect=ConnectionError("private broker details"),
        ),
        pytest.raises(ConnectionError),
    ):
        queue_ownership_snapshot(world.project.pk, [system.solar_system_id])
    system.refresh_from_db()
    assert system.ownership_status == "Last attempt failed"
    assert "Could not queue" in system.owner_error
    assert "private broker" not in system.owner_error
    with patch("aasov.tasks.snapshot_ownership.delay"):
        queue_ownership_snapshot(world.project.pk, [system.solar_system_id])
    system.refresh_from_db()
    assert system.ownership_status == "Queued; waiting for worker"


def test_http_error_records_stage_and_attempt_time(world):
    system = world.system("Pending")
    response = requests.Response()
    response.status_code = 503
    with (
        patch("aasov.tasks.requests.get", return_value=response),
        pytest.raises(requests.HTTPError),
    ):
        capture_ownership(world.project.pk, [system.solar_system_id])
    system.refresh_from_db()
    assert system.owner_attempted_at and not system.owner_observed_at
    assert system.owner_error == "ESI sovereignty lookup failed (HTTP 503)."


def test_partial_map_captures_available_systems_and_only_retries_missing(world):
    a, b = world.system("Known"), world.system("Missing")
    response = Mock()
    response.json.return_value = {
        "solar_systems": [{"solar_system_id": a.solar_system_id, "claim": {"unclaimed": True}}]
    }
    with (
        patch("aasov.tasks.requests.get", return_value=response),
        pytest.raises(ValueError, match="omitted"),
    ):
        capture_ownership(world.project.pk, [a.solar_system_id, b.solar_system_id])
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.ownership_status == "Captured" and a.owner_name == "Unclaimed" and not a.owner_error
    assert b.ownership_status == "Last attempt failed" and b.owner_kind == "unknown"
    with patch("aasov.tasks.snapshot_ownership.delay") as task:
        assert (
            queue_ownership_snapshot(world.project.pk, [a.solar_system_id, b.solar_system_id]) == 1
        )
        task.assert_called_once_with(world.project.pk, [b.solar_system_id])


def test_now_command_bypasses_celery_and_reports_failures(world):
    system = world.system("Pending")
    response = Mock()
    response.json.return_value = {
        "solar_systems": [{"solar_system_id": system.solar_system_id, "claim": {"unclaimed": True}}]
    }
    out = StringIO()
    with (
        patch("aasov.tasks.requests.get", return_value=response),
        patch("aasov.tasks.snapshot_ownership.delay") as task,
    ):
        call_command(
            "aasov_snapshot_owners", "--now", "--project", str(world.project.pk), stdout=out
        )
        task.assert_not_called()
    assert "captured 1" in out.getvalue()
    other = world.system("Pending failure")
    with (
        patch("aasov.tasks.requests.get", side_effect=requests.Timeout),
        pytest.raises(CommandError, match="Timeout"),
    ):
        call_command("aasov_snapshot_owners", "--now", "--project", str(world.project.pk))
    other.refresh_from_db()
    assert "Timeout" in other.owner_error
