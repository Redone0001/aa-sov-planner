import pytest
from django.test import Client
from django.urls import reverse

from aasov.models import PlannedUpgrade, Project, WorkforceRoute
from aasov.services import calculate_project, save_route

pytestmark = pytest.mark.django_db
AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


@pytest.mark.parametrize("status", ["planned", "online", "temporary", "offline"])
def test_offline_keeps_upgrade_and_refreshes_both_budgets(client, editor, world, status):
    system = world.system("A")
    upgrade = world.upgrade(power_allocation=2000, workforce_allocation=20000)
    item = PlannedUpgrade.objects.create(system=system, upgrade=upgrade, status=status)
    url = reverse("aasov:upgrade_offline", args=[world.project.pk, system.pk, item.pk])
    client.force_login(editor)
    assert client.get(url).status_code == 405
    response = client.post(url, **AJAX)
    assert response.status_code == 200 and response.json()["saved"]
    item.refresh_from_db()
    assert item.status == "offline"
    b = calculate_project(world.project)["budgets"][0]
    assert not b.warnings
    assert b.power_left == b.current.power_left == 1000
    assert b.workforce_left == b.current.workforce_left == 10000
    assert "Offline" in response.json()["board"]
    assert client.post(url, **AJAX).status_code == 200


def test_offline_permissions_scoping_and_csrf(client, reader, world):
    system = world.system("A")
    item = PlannedUpgrade.objects.create(system=system, upgrade=world.upgrade())
    url = reverse("aasov:upgrade_offline", args=[world.project.pk, system.pk, item.pk])
    client.force_login(reader)
    assert client.post(url, **AJAX).status_code == 403
    from django.contrib.auth.models import Permission

    reader.user_permissions.add(
        Permission.objects.get(codename="edit_plan", content_type__app_label="aasov")
    )
    other = Project.objects.create(name="Other")
    wrong = reverse("aasov:upgrade_offline", args=[other.pk, system.pk, item.pk])
    assert client.post(wrong, **AJAX).status_code == 404
    wrong_system = world.system("B")
    wrong = reverse("aasov:upgrade_offline", args=[world.project.pk, wrong_system.pk, item.pk])
    assert client.post(wrong, **AJAX).status_code == 404
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(reader)
    assert csrf_client.post(url, **AJAX).status_code == 403
    item.refresh_from_db()
    assert item.status == "planned"


def test_map_actions_permission_and_delete_refresh(client, reader, world):
    a, b = world.system("A"), world.system("B")
    world.connect(a, b)
    item = PlannedUpgrade.objects.create(system=a, upgrade=world.upgrade(), status="temporary")
    route = save_route(world.project.pk, a, b, 100, configure_modes=True)
    url = reverse("aasov:map_data", args=[world.project.pk])
    client.force_login(reader)
    data = client.get(url).json()
    u = next(n for n in data["nodes"] if n["id"] == a.solar_system_id)["upgrades"][0]
    assert u["offline"] is None and u["remove"] is None
    assert data["routes"][0]["remove"] is None
    from django.contrib.auth.models import Permission

    reader.user_permissions.add(
        Permission.objects.get(codename="edit_plan", content_type__app_label="aasov")
    )
    data = client.get(url).json()
    u = next(n for n in data["nodes"] if n["id"] == a.solar_system_id)["upgrades"][0]
    assert u["offline"] and u["remove"]
    assert client.post(data["routes"][0]["remove"], **AJAX).json()["saved"]
    assert not WorkforceRoute.objects.filter(pk=route.pk).exists()
    assert client.post(u["remove"], **AJAX).json()["saved"]
    assert not PlannedUpgrade.objects.filter(pk=item.pk).exists()
    assert client.get(url).json()["routes"] == []
