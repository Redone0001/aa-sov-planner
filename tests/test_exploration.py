import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from aasov.exploration import coverage
from aasov.map_data import LIGHT_YEAR
from aasov.models import LogisticsLink, PlannedUpgrade, Project

pytestmark = pytest.mark.django_db


def endpoint(world, name, *args):
    return reverse(f"aasov:{name}", args=[world.project.pk, *args])


def infrastructure(world, system, status="planned"):
    upgrade = world.upgrade(name="Advanced Logistics Network")
    PlannedUpgrade.objects.create(system=system, upgrade=upgrade, status=status)
    solar = system.solar_system
    solar.x, solar.y, solar.z = 0, 0, 0
    solar.save()


def test_five_jump_boundary_and_paths_outside_plan(world):
    nodes = [world.system(str(i)) for i in range(7)]
    for a, b in zip(nodes, nodes[1:]):
        world.connect(a, b)
    nodes[2].delete()  # The SDE topology still contains this transit system.
    result = coverage([nodes[0].solar_system_id])
    assert result[nodes[5].solar_system_id] == 5
    assert nodes[6].solar_system_id not in result
    assert coverage([]) == {}


def test_link_validation_permissions_and_delete(client, reader, world):
    a, b = world.system("A"), world.system("B")
    infrastructure(world, a)
    infrastructure(world, b, "online")
    url = endpoint(world, "logistics_link")
    client.force_login(reader)
    assert client.post(url, {"source": a.pk, "destination": b.pk}).status_code == 403
    reader.user_permissions.add(Permission.objects.get(codename="edit_plan"))
    client.force_login(reader)
    assert client.post(url, {"source": b.pk, "destination": a.pk}).status_code == 302
    link = LogisticsLink.objects.get()
    assert link.source_id < link.destination_id
    assert client.post(url, {"source": a.pk, "destination": b.pk}).status_code == 200
    assert LogisticsLink.objects.count() == 1
    assert client.post(url, {"source": a.pk, "destination": a.pk}).status_code == 200
    solar = b.solar_system
    solar.x = 5.01 * LIGHT_YEAR
    solar.save()
    link.delete()
    assert client.post(url, {"source": a.pk, "destination": b.pk}).status_code == 200
    assert not LogisticsLink.objects.exists()
    solar.x = 5 * LIGHT_YEAR
    solar.save()
    assert client.post(url, {"source": a.pk, "destination": b.pk}).status_code == 302
    link = LogisticsLink.objects.get()
    assert client.get(endpoint(world, "logistics_remove", link.pk)).status_code == 405
    assert client.post(endpoint(world, "logistics_remove", link.pk)).status_code == 302
    assert not LogisticsLink.objects.exists()


def test_status_filter_and_project_scope(client, editor, world):
    a, b = world.system("A"), world.system("B")
    infrastructure(world, a)
    infrastructure(world, b, "temporary")
    client.force_login(editor)
    url = endpoint(world, "logistics_link")
    assert client.post(url, {"source": a.pk, "destination": b.pk}).status_code == 200
    assert not LogisticsLink.objects.exists()
    b.upgrades.update(status="online")
    b.project = Project.objects.create(name="Other")
    b.save()
    assert client.post(url, {"source": a.pk, "destination": b.pk}).status_code == 200
    assert not LogisticsLink.objects.exists()


def test_map_coverage_hidden_planned_and_invalid_links(client, reader, world):
    a, b = world.system("A"), world.system("B")
    infrastructure(world, a)
    infrastructure(world, b, "online")
    array = PlannedUpgrade.objects.create(
        system=a, upgrade=world.upgrade(name="Exploration Detector III"), status="planned"
    )
    link = LogisticsLink.objects.create(source=a, destination=b)
    reader.user_permissions.add(Permission.objects.get(codename="edit_plan"))
    client.force_login(reader)
    url = endpoint(world, "map_data")
    payload = client.get(url).json()
    nodes = {n["id"]: n for n in payload["nodes"]}
    assert nodes[a.solar_system_id]["exploration_jumps"] == 0
    assert nodes[b.solar_system_id]["exploration_jumps"] is None
    assert nodes[b.solar_system_id]["exploration_jumps"] is None
    assert payload["logistics_links"][0]["valid"]
    a.upgrades.exclude(pk=array.pk).update(status="offline")
    payload = client.get(url).json()
    assert not payload["logistics_links"][0]["valid"]
    assert all(
        n["exploration_jumps"] is None for n in payload["nodes"] if n["id"] == b.solar_system_id
    )
    reader.user_permissions.remove(Permission.objects.get(codename="edit_plan"))
    world.project.simplified_viewers = True
    world.project.save()
    client.force_login(reader)
    payload = client.get(url).json()
    assert payload["logistics_links"] == []
    assert all(n["exploration_jumps"] is None for n in payload["nodes"])
    array.status = "temporary"
    array.save()
    assert client.get(url).json()["nodes"][0]["exploration"]
    a.delete()
    assert not LogisticsLink.objects.filter(pk=link.pk).exists()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Exploration Detector 1", True),
        ("Exploration Detector II", True),
        ("Guristas Exploration Detector III", True),
        ("Major Threat Detection Array III", False),
    ],
)
def test_exploration_detector_tiers(world, name, expected):
    from aasov.exploration import is_exploration

    assert is_exploration(world.upgrade(name=name)) is expected
