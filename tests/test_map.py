import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.urls import reverse

from aasov.map_data import LIGHT_YEAR, distance, zone
from aasov.models import PlannedUpgrade, Project
from aasov.services import remove_system, save_route

pytestmark = pytest.mark.django_db


def place(system, x=0, y=0, z=0, x2=0, y2=0):
    solar = system.solar_system
    solar.x, solar.y, solar.z = (n * LIGHT_YEAR for n in (x, y, z))
    solar.x_2d, solar.y_2d = x2, y2
    solar.save()
    return solar


def endpoint(name, world, *args):
    return reverse(f"aasov:{name}", args=[world.project.pk, *args])


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, None),
        (0, 1),
        (5, 1),
        (5.001, 2),
        (10, 2),
        (10.001, 3),
        (15, 3),
        (15.001, 4),
        (20, 4),
        (20.001, 5),
        (100, 5),
    ],
)
def test_exact_zone_boundaries(value, expected):
    assert zone(value) == expected


def test_3d_range_includes_external_systems_and_excludes_ineligible(client, reader, world):
    origin = world.system("Capital")
    source = place(origin)
    edge = place(world.system("At five"), x=3, y=4, x2=100000, y2=-500000)
    place(world.system("Beyond five"), x=3, y=4, z=0.01)
    external = place(world.system("External"), z=4.9)
    world.project.systems.filter(solar_system=external).delete()
    npc = place(world.system("NPC"), x=1)
    npc.faction_id_raw = 500001
    npc.save()
    world.project.capital = origin
    world.project.save()
    client.force_login(reader)
    payload = client.get(endpoint("map_range", world, source.pk)).json()
    nodes = {n["id"]: n for n in payload["nodes"]}
    assert set(nodes) == {edge.pk, external.pk}
    assert nodes[edge.pk]["source_distance"] == 5
    assert nodes[edge.pk]["zone"] == 1
    assert nodes[external.pk]["source_distance"] == 4.9
    assert distance(source, edge) == 5


def test_map_overlays_and_editor_permissions(client, reader, world):
    a, t, b = [world.system(n) for n in ("Source", "Transit", "Destination")]
    place(a, x2=123, y2=456)
    place(t, x=5, x2=234, y2=567)
    place(b, x=10.01, x2=345, y2=678)
    world.connect(a, t)
    world.connect(t, b)
    save_route(world.project.pk, a, b, 600, configure_modes=True)
    upgrade = world.upgrade(name="Advanced Logistics Network", power_allocation=2000)
    PlannedUpgrade.objects.create(system=a, upgrade=upgrade, status="planned")
    world.project.capital = a
    world.project.save()
    client.force_login(reader)
    payload = client.get(endpoint("map_data", world)).json()
    nodes = {n["id"]: n for n in payload["nodes"]}
    assert nodes[a.solar_system_id]["position"] == [123, 456]
    assert nodes[b.solar_system_id]["zone"] == 3
    assert nodes[a.solar_system_id]["power"]["left"] == -1000
    assert nodes[a.solar_system_id]["warnings"]
    assert nodes[a.solar_system_id]["logistics"] == "planned"
    assert nodes[a.solar_system_id]["upgrades"][0]["type_id"] == upgrade.item_type_id
    assert all(url is None for url in nodes[a.solar_system_id]["actions"].values())
    assert nodes[a.solar_system_id]["upgrades"][0]["edit"] is None
    assert payload["routes"][0]["path"] == [a.solar_system_id, t.solar_system_id, b.solar_system_id]
    assert payload["routes"][0]["valid"]
    assert payload["routes"][0]["amount"] == 600
    assert len(payload["gates"]) == 2
    reader.user_permissions.add(Permission.objects.get(codename="edit_plan"))
    payload = client.get(endpoint("map_data", world)).json()
    node = next(n for n in payload["nodes"] if n["id"] == a.solar_system_id)
    assert node["upgrades"][0]["install"]
    assert node["actions"]["Add upgrade"]
    assert node["actions"]["Remove system"] is None


def test_missing_coordinates_are_not_zero_and_empty_plan(client, reader, world):
    client.force_login(reader)
    assert client.get(endpoint("map_data", world)).json()["nodes"] == []
    missing = world.system("Missing")
    world.project.capital = missing
    world.project.save()
    payload = client.get(endpoint("map_data", world)).json()
    assert payload["nodes"][0]["position"] is None
    assert payload["nodes"][0]["capital_distance"] is None
    assert payload["nodes"][0]["zone"] is None
    result = client.get(endpoint("map_range", world, missing.solar_system_id)).json()
    assert result["error"] and not result["nodes"]


def test_capital_manager_only_and_project_scoped(client, editor, world):
    system = world.system("Capital")
    url = endpoint("capital", world)
    client.force_login(editor)
    assert client.post(url, {"capital": system.pk}).status_code == 403
    editor.user_permissions.add(Permission.objects.get(codename="manage_plan"))
    other_project = Project.objects.create(name="Other")
    other = world.system("Other capital")
    other.project = other_project
    other.save()
    invalid = client.post(url, {"capital": other.pk}, HTTP_X_REQUESTED_WITH="XMLHttpRequest").json()
    assert not invalid["saved"]
    assert client.post(url, {"capital": system.pk}).status_code == 302
    world.project.refresh_from_db()
    assert world.project.capital_id == system.pk
    assert client.post(url, {"capital": ""}).status_code == 302
    world.project.refresh_from_db()
    assert world.project.capital_id is None
    world.project.capital = other
    with pytest.raises(ValidationError):
        world.project.clean()
    world.project.capital = system
    world.project.save()
    remove_system(world.project.pk, system.pk)
    world.project.refresh_from_db()
    assert world.project.capital_id is None


def test_map_requires_read_access(client, django_user_model, world):
    system = place(world.system("A"))
    for name, args in (("map_data", []), ("map_range", [system.pk])):
        assert client.get(endpoint(name, world, *args)).status_code == 302
    user = django_user_model.objects.create_user("no-map-access")
    client.force_login(user)
    assert client.get(endpoint("map_data", world)).status_code == 403
    assert client.get(endpoint("map_range", world, system.pk)).status_code == 403
