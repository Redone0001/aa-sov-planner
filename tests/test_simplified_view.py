import json

import pytest
from bs4 import BeautifulSoup
from django.contrib.auth.models import Permission
from django.urls import reverse

from aasov.models import PlannedUpgrade
from aasov.services import save_route

pytestmark = pytest.mark.django_db


@pytest.fixture
def simplified_plan(world):
    a, b = world.system("Alpha"), world.system("Beta")
    world.connect(a, b)
    save_route(world.project.pk, a, b, 4321, configure_modes=True)
    entries = [
        ("Visible online", "online", {}),
        ("Visible temporary", "temporary", {}),
        ("Hidden planned", "planned", {}),
        ("Hidden offline", "offline", {}),
        ("Hidden power booster", "online", {"power_production": 1234}),
        ("Hidden workforce booster", "temporary", {"workforce_production": 9876}),
        ("Advanced Logistics Network", "planned", {}),
    ]
    for name, status, costs in entries:
        upgrade = world.upgrade(name=name, power_allocation=2000, **costs)
        PlannedUpgrade.objects.create(system=a, upgrade=upgrade, status=status)
    world.project.simplified_viewers = True
    world.project.save()
    return a, b


def test_viewer_html_map_and_clipboard_exclude_hidden_planning_data(
    world, reader, client, simplified_plan
):
    client.force_login(reader)
    response = client.get(reverse("aasov:project", args=[world.project.pk]))
    assert response.status_code == 200
    html = response.content.decode()
    for hidden in (
        "Hidden planned",
        "Hidden offline",
        "Hidden power booster",
        "Hidden workforce booster",
        "Advanced Logistics Network",
        "4,321",
        "Power deficit",
        'data-map-layer="routes"',
        'data-map-layer="warnings"',
        'role="progressbar"',
    ):
        assert hidden not in html, hidden
    assert "Visible online" in html and "Visible temporary" in html
    clipboard = json.loads(
        BeautifulSoup(html, "html.parser").select_one("#sov-inventory-clipboard").string
    )
    assert clipboard == {"visible": "Visible online\t1\nVisible temporary\t1"}
    data = client.get(reverse("aasov:map_data", args=[world.project.pk])).json()
    assert data["simplified_view"] and data["routes"] == []
    a = next(n for n in data["nodes"] if n["id"] == simplified_plan[0].solar_system_id)
    assert {u["name"] for u in a["upgrades"]} == {"Visible online", "Visible temporary"}
    assert a["logistics"] is None
    assert all(field not in a for field in ("power", "workforce", "mode", "warnings"))
    assert all(not url for url in a["actions"].values())


@pytest.mark.parametrize("role", ["editor", "manager", "administrator", "superuser"])
def test_privileged_roles_retain_full_view(world, reader, client, simplified_plan, role):
    codes = {"editor": "edit_plan", "manager": "manage_plan", "administrator": "change_project"}
    if role == "superuser":
        reader.is_superuser = True
    else:
        reader.user_permissions.add(
            Permission.objects.get(content_type__app_label="aasov", codename=codes[role])
        )
    reader.is_staff = role == "administrator"
    reader.save()
    client.force_login(reader)
    html = client.get(reverse("aasov:project", args=[world.project.pk])).content.decode()
    assert "Hidden planned" in html and "Hidden power booster" in html
    assert "4,321" in html and 'role="progressbar"' in html
    data = client.get(reverse("aasov:map_data", args=[world.project.pk])).json()
    assert not data["simplified_view"] and data["routes"][0]["amount"] == 4321
    assert "workforce" in data["nodes"][0]


def test_disabling_flag_restores_viewer_details(world, reader, client, simplified_plan):
    world.project.simplified_viewers = False
    world.project.save()
    client.force_login(reader)
    data = client.get(reverse("aasov:map_data", args=[world.project.pk])).json()
    assert not data["simplified_view"]
    assert data["routes"][0]["amount"] == 4321
    assert len(data["nodes"][0]["upgrades"]) == 7


def test_staff_viewer_cannot_read_workforce_mode_in_admin(world, reader, client, simplified_plan):
    reader.is_staff = True
    reader.save()
    client.force_login(reader)
    response = client.get(reverse("admin:aasov_plannedsystem_change", args=[simplified_plan[0].pk]))
    assert response.status_code == 200
    assert "field-mode" not in response.content.decode()


def test_simplified_flag_does_not_bypass_visibility(world, reader, client, simplified_plan):
    world.project.restrict_access = True
    world.project.allow_editors = True
    world.project.save()
    client.force_login(reader)
    assert client.get(reverse("aasov:project", args=[world.project.pk])).status_code == 404
    assert client.get(reverse("aasov:map_data", args=[world.project.pk])).status_code == 404
