from unittest.mock import Mock, patch

import pytest
from bs4 import BeautifulSoup
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from eve_sde.models import Constellation

from aasov.models import PlannedUpgrade, Project, WorkforceRoute
from aasov.optimizer import apply_proposal, preview_rows, propose
from aasov.services import calculate_project, remove_system, save_route, save_upgrade, sync_project
from aasov.tasks import snapshot_ownership

pytestmark = pytest.mark.django_db
AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def url(name, *args):
    return reverse(f"aasov:{name}", args=args)


def ratting(world, kind, level, power, workforce, **kwargs):
    return world.upgrade(
        f"{kind} Threat Detection Array {level}",
        power_allocation=power,
        workforce_allocation=workforce,
        **kwargs,
    )


def test_status_remembered_across_systems_but_existing_edit_keeps_status(world, editor, client):
    a, b = world.system("A"), world.system("B")
    u = world.upgrade()
    existing = save_upgrade(world.project.pk, b.pk, u, "planned")
    client.force_login(editor)
    client.post(
        url("upgrade_add", world.project.pk, a.pk), {"upgrade": u.pk, "status": "online"}, **AJAX
    )
    html = client.get(url("upgrade_add", world.project.pk, b.pk), **AJAX).json()["html"]
    assert (
        BeautifulSoup(html, "html.parser").select_one("#id_status option[selected]")["value"]
        == "online"
    )
    html = client.get(url("upgrade_edit", world.project.pk, b.pk, existing.pk), **AJAX).json()[
        "html"
    ]
    assert (
        BeautifulSoup(html, "html.parser").select_one("#id_status option[selected]")["value"]
        == "planned"
    )


def test_manager_removal_permission_csrf_scope_and_persistent_exclusion(world, editor, client):
    a = world.system("A")
    endpoint = url("system_remove", world.project.pk, a.pk)
    client.force_login(editor)
    assert client.post(endpoint).status_code == 403
    editor.user_permissions.add(
        Permission.objects.get(content_type__app_label="aasov", codename="manage_plan")
    )
    assert client.get(endpoint, **AJAX).json()["saved"] is False
    assert world.project.systems.filter(pk=a.pk).exists()
    other = Project.objects.create(name="Other")
    assert client.post(url("system_remove", other.pk, a.pk)).status_code == 404
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(editor)
    assert secure.post(endpoint).status_code == 403
    assert client.post(endpoint, **AJAX).json()["saved"]
    world.project.regions.add(world.region)
    sync_project(world.project)
    assert not world.project.systems.exists()
    assert world.project.excluded_systems.filter(pk=a.solar_system_id).exists()


def test_removing_transit_keeps_visible_invalid_route(world):
    a, b, c = world.system("A", "export"), world.system("B"), world.system("C", "import")
    world.connect(a, b)
    world.connect(b, c)
    save_route(world.project.pk, a, c, 100)
    remove_system(world.project.pk, b.pk)
    data = calculate_project(world.project)
    assert not data["routes"][0]["valid"]
    remove_system(world.project.pk, a.pk)
    assert not WorkforceRoute.objects.exists()


def test_owner_snapshot_is_batched_and_never_overwrites(world):
    a, b = world.system("A"), world.system("B")
    response = Mock()
    response.json.return_value = {
        "solar_systems": [
            {"solar_system_id": a.solar_system_id, "claim": {"alliance": {"alliance_id": 990001}}},
            {"solar_system_id": b.solar_system_id, "claim": {"unclaimed": True}},
        ]
    }
    names = Mock()
    names.json.return_value = [{"id": 990001, "name": "Test Alliance"}]
    with (
        patch("aasov.tasks.requests.get", return_value=response) as get,
        patch("aasov.tasks.requests.post", return_value=names) as post,
    ):
        snapshot_ownership.run(world.project.pk, [a.solar_system_id, b.solar_system_id])
        snapshot_ownership.run(world.project.pk, [a.solar_system_id, b.solar_system_id])
        assert get.call_count == post.call_count == 1
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.owner_name == "Test Alliance" and a.owner_id == 990001
    assert a.owner_observed_at and b.owner_kind == "unclaimed"


def test_creation_queues_only_new_systems_after_commit(world, django_capture_on_commit_callbacks):
    a = world.system("A")
    plan = Project.objects.create(name="New plan")
    plan.regions.add(world.region)
    with patch("aasov.tasks.snapshot_ownership.delay") as task:
        with django_capture_on_commit_callbacks(execute=True):
            sync_project(plan)
        task.assert_called_once_with(plan.pk, [a.solar_system_id])
        with django_capture_on_commit_callbacks(execute=True):
            sync_project(plan)
        assert task.call_count == 1


def test_ratting_prefers_minor_three_over_major_two_and_two_smaller(world):
    a = world.system("A", power=1000, workforce=1000)
    high = ratting(world, "Minor", "III", 1000, 1000)
    ratting(world, "Major", "II", 500, 500)
    ratting(world, "Minor", "II", 500, 500)
    proposal = propose(world.project, a.solar_system.constellation_id)
    assert proposal["selected"][str(a.pk)] == [high.pk]
    assert proposal["optimal"]
    assert not PlannedUpgrade.objects.exists()  # Preview has no side effects.
    assert preview_rows(world.project, proposal)["optimisation_rows"][0]["power"] == 0
    apply_proposal(world.project.pk, proposal)
    assert a.upgrades.get().upgrade_id == high.pk


def test_ratting_major_wins_equal_level_and_replaced_online_goes_offline(world):
    a = world.system("A", power=1000, workforce=1000)
    minor = ratting(world, "Minor", "III", 1000, 1000)
    major = ratting(world, "Major", "III", 1000, 1000)
    save_upgrade(world.project.pk, a.pk, minor, "online")
    proposal = propose(world.project, a.solar_system.constellation_id)
    assert proposal["selected"][str(a.pk)] == [major.pk]
    apply_proposal(world.project.pk, proposal)
    assert a.upgrades.get(upgrade=minor).status == "offline"
    assert a.upgrades.get(upgrade=major).status == "planned"


def test_optimizer_builds_transit_path_and_preserves_other_upgrades(world):
    source = world.system("Source", power=0, workforce=1000)
    bridge = world.system("Bridge", power=0, workforce=0)
    dest = world.system("Dest", power=1000, workforce=0)
    world.connect(source, bridge)
    world.connect(bridge, dest)
    best = ratting(world, "Major", "III", 800, 900)
    utility = world.upgrade("Utility", power_allocation=200, workforce_allocation=0)
    save_upgrade(world.project.pk, dest.pk, utility, "online")
    proposal = propose(world.project, dest.solar_system.constellation_id)
    assert proposal["selected"][str(dest.pk)] == [best.pk]
    assert proposal["routes"] == [[source.pk, dest.pk, 900]]
    assert proposal["modes"][str(bridge.pk)] == "transit"
    apply_proposal(world.project.pk, proposal)
    assert dest.upgrades.get(upgrade=utility).status == "online"
    assert all(not b.warnings for b in calculate_project(world.project)["budgets"])


def test_optimizer_no_teleport_and_no_export_of_generated_workforce(world):
    source = world.system("Source", power=0, workforce=0)
    dest = world.system("Dest", power=1000, workforce=0)
    ratting(world, "Major", "III", 800, 900)
    proposal = propose(world.project, dest.solar_system.constellation_id)
    assert not any(proposal["selected"].values())
    world.connect(source, dest)
    generator = world.upgrade("Generator", workforce_production=1000)
    save_upgrade(world.project.pk, source.pk, generator, "online")
    proposal = propose(world.project, dest.solar_system.constellation_id)
    assert not any(proposal["selected"].values())
    assert not proposal["routes"]


def test_optimizer_import_cap_and_stale_preview(world):
    dest = world.system("Dest", power=1000, workforce=0)
    for i in range(4):
        source = world.system(f"Source {i}", power=0, workforce=100)
        world.connect(source, dest)
    high = ratting(world, "Major", "III", 1000, 400)
    low = ratting(world, "Minor", "II", 1000, 300)
    proposal = propose(world.project, dest.solar_system.constellation_id)
    assert proposal["selected"][str(dest.pk)] == [low.pk]
    assert len(proposal["routes"]) == 3
    high.workforce_allocation = 300
    high.save()
    with pytest.raises(ValidationError, match="changed"):
        apply_proposal(world.project.pk, proposal)
    assert not PlannedUpgrade.objects.exists()


def test_optimizer_preserves_boundary_routes(world):
    source = world.system("Source", "export", power=0, workforce=1000)
    bridge = world.system("Bridge", power=0, workforce=0)
    dest = world.system("Outside", "import", power=1000, workforce=0)
    other = Constellation.objects.create(id=20000002, name="Other", region=world.region)
    dest.solar_system.constellation = other
    dest.solar_system.save()
    world.connect(source, bridge)
    world.connect(bridge, dest)
    route = save_route(world.project.pk, source, dest, 500)
    ratting(world, "Major", "III", 1000, 100)
    proposal = propose(world.project, source.solar_system.constellation_id)
    assert route.pk not in proposal["remove_routes"]
    assert proposal["modes"][str(bridge.pk)] == "transit"
    apply_proposal(world.project.pk, proposal)
    route.refresh_from_db()
    assert route.amount == 500


def test_optimizer_permissions_preview_apply_and_invalid_catalog(world, reader, client):
    a = world.system("A")
    endpoint = url("best_ratting", world.project.pk, a.solar_system.constellation_id)
    client.force_login(reader)
    assert client.get(endpoint).status_code == 403
    reader.user_permissions.add(
        Permission.objects.get(content_type__app_label="aasov", codename="edit_plan")
    )
    response = client.get(endpoint, **AJAX)
    assert "No Major/Minor" in response.json()["html"]
    ratting(world, "Major", "III", 500, 100)
    response = client.get(endpoint, **AJAX)
    token = BeautifulSoup(response.json()["html"], "html.parser").select_one("[name=proposal]")[
        "value"
    ]
    assert "invalid" in client.post(endpoint, {"proposal": "tampered"}, **AJAX).json()["html"]
    response = client.post(endpoint, {"proposal": token}, **AJAX)
    assert response.json()["saved"]
    assert a.upgrades.count() == 1


def test_optimizer_one_export_destination_and_exclusivity(world):
    source = world.system("Source", power=0, workforce=400)
    a, b = world.system("A", workforce=0), world.system("B", workforce=0)
    world.connect(source, a)
    world.connect(source, b)
    ratting(world, "Major", "III", 500, 200, mutually_exclusive_group="threat")
    ratting(world, "Minor", "III", 500, 200, mutually_exclusive_group="threat")
    proposal = propose(world.project, a.solar_system.constellation_id)
    assert sum(len(ids) for ids in proposal["selected"].values()) == 1
    assert len(proposal["routes"]) == 1
    apply_proposal(world.project.pk, proposal)


def test_optimizer_refuses_incomplete_resources_and_preserved_deficits(world):
    a = world.system("A", power=1000)
    ratting(world, "Major", "III", 500, 200)
    utility = world.upgrade("Utility", power_allocation=1001)
    save_upgrade(world.project.pk, a.pk, utility, "online")
    with pytest.raises(ValidationError, match="No feasible"):
        propose(world.project, a.solar_system.constellation_id)
    a.upgrades.all().delete()
    from eve_sde.models import StarResource

    StarResource.objects.all().delete()
    with pytest.raises(ValidationError, match="incomplete"):
        propose(world.project, a.solar_system.constellation_id)


def test_owner_missing_response_is_not_mislabelled_unclaimed(world):
    a = world.system("A")
    response = Mock()
    response.json.return_value = {"solar_systems": []}
    with (
        patch("aasov.tasks.requests.get", return_value=response),
        pytest.raises(ValueError, match="omitted"),
    ):
        # Test the underlying function so Celery does not schedule its retry.
        snapshot_ownership._orig_run(world.project.pk, [a.solar_system_id])
    a.refresh_from_db()
    assert a.owner_kind == "unknown" and a.owner_observed_at is None


def test_manager_can_remove_without_staff_or_edit_permission(world, reader, client):
    a = world.system("A")
    reader.user_permissions.add(
        Permission.objects.get(content_type__app_label="aasov", codename="manage_plan")
    )
    client.force_login(reader)
    html = client.get(url("project", world.project.pk)).content.decode()
    assert 'id="sov-editor"' in html and "Remove system" in html
    assert "Add upgrade" not in html
    assert not reader.is_staff
    assert client.post(url("system_remove", world.project.pk, a.pk), **AJAX).json()["saved"]


def test_optimizer_rejects_preserved_non_ratting_exclusivity_conflicts(world):
    a = world.system("A")
    ratting(world, "Major", "III", 500, 200)
    for tier in ["I", "II"]:
        u = world.upgrade(f"Mining Upgrade {tier}", power_allocation=0)
        save_upgrade(world.project.pk, a.pk, u, "online")
    with pytest.raises(ValidationError, match="conflicting non-ratting"):
        propose(world.project, a.solar_system.constellation_id)
