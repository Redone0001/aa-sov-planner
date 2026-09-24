import pytest
from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.urls import reverse

from aasov.models import PlannedUpgrade, WorkforceRoute
from aasov.services import calculate_project, save_route
from aasov.workforce import apply_proposal, propose

pytestmark = pytest.mark.django_db


def consume(world, system, amount):
    upgrade = world.upgrade(name=f"Consumer {system.pk}", workforce_allocation=amount)
    return PlannedUpgrade.objects.create(system=system, upgrade=upgrade)


def test_global_assignment_covers_scarce_neighbors_without_changing_upgrades(world):
    a, b = world.system("A", workforce=100), world.system("B", workforce=100)
    x, y = world.system("X", workforce=100), world.system("Y", workforce=100)
    consume(world, x, 200)
    consume(world, y, 200)
    world.connect(a, x)
    world.connect(a, y)
    world.connect(b, x)
    proposal, preview = propose(world.project)
    assert preview["covered"] == 200 and preview["remaining"] == 0
    assert not WorkforceRoute.objects.exists()
    before = list(PlannedUpgrade.objects.values_list("pk", "status"))
    apply_proposal(world.project.pk, proposal)
    assert set(WorkforceRoute.objects.values_list("source_id", "destination_id", "amount")) == {
        (a.pk, y.pk, 100),
        (b.pk, x.pk, 100),
    }
    assert list(PlannedUpgrade.objects.values_list("pk", "status")) == before
    assert all(b.workforce_left == 0 for b in calculate_project(world.project)["budgets"])


def test_generated_workforce_cannot_be_exported_and_partial_coverage(world):
    a, b = world.system("A", workforce=100), world.system("B", workforce=100)
    producer = world.upgrade(workforce_production=500)
    PlannedUpgrade.objects.create(system=a, upgrade=producer)
    consume(world, b, 350)
    world.connect(a, b)
    proposal, preview = propose(world.project)
    assert preview["covered"] == 100 and preview["remaining"] == 150
    apply_proposal(world.project.pk, proposal)
    assert WorkforceRoute.objects.get().amount == 100


def test_increase_existing_route_at_three_source_limit(world):
    receiver = world.system("Receiver", workforce=100)
    consume(world, receiver, 160)
    donors = [world.system(str(i), workforce=100) for i in range(4)]
    for donor in donors:
        world.connect(donor, receiver)
    for donor in donors[:3]:
        save_route(world.project.pk, donor, receiver, 10, configure_modes=True)
    ids = set(WorkforceRoute.objects.values_list("pk", flat=True))
    proposal, preview = propose(world.project)
    assert preview["covered"] == 30
    assert all(r[3] in ids for r in proposal["routes"])
    apply_proposal(world.project.pk, proposal)
    assert set(WorkforceRoute.objects.values_list("pk", flat=True)) == ids


def test_new_routes_limited_to_three_sources_and_adjacent_systems(world):
    receiver = world.system("Receiver", workforce=100)
    consume(world, receiver, 150)
    for i in range(4):
        donor = world.system(str(i), workforce=10)
        world.connect(donor, receiver)
    world.system("Disconnected", workforce=10000)
    proposal, preview = propose(world.project)
    assert len(proposal["routes"]) == 3
    assert preview["covered"] == 30 and preview["remaining"] == 20


def test_occupied_transit_system_is_protected(world):
    a, t, b, x = [world.system(n, workforce=100) for n in ("A", "Transit", "B", "X")]
    world.connect(a, t)
    world.connect(t, b)
    world.connect(t, x)
    consume(world, x, 150)
    save_route(world.project.pk, a, b, 10, configure_modes=True)
    proposal, preview = propose(world.project)
    assert proposal["routes"] == [] and preview["remaining"] == 50
    t.refresh_from_db()
    assert t.mode == "transit"


def test_stale_preview_rejected_atomically(world):
    a, b = world.system("A"), world.system("B")
    item = consume(world, b, 15000)
    world.connect(a, b)
    proposal, _ = propose(world.project)
    item.status = "offline"
    item.save()
    with pytest.raises(ValidationError, match="changed"):
        apply_proposal(world.project.pk, proposal)
    assert not WorkforceRoute.objects.exists()


def test_preview_and_apply_permissions_and_signature(client, editor, world, django_user_model):
    a, b = world.system("A"), world.system("B")
    consume(world, b, 15000)
    world.connect(a, b)
    url = reverse("aasov:balance_workforce", args=[world.project.pk])
    client.force_login(editor)
    ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
    response = client.get(url, **ajax).json()
    token = BeautifulSoup(response["html"], "html.parser").select_one('[name="proposal"]')["value"]
    assert not WorkforceRoute.objects.exists()
    bad = client.post(url, {"proposal": token + "x"}, **ajax).json()
    assert not bad["saved"] and not WorkforceRoute.objects.exists()
    assert client.post(url, {"proposal": token}, **ajax).json()["saved"]
    user = django_user_model.objects.create_user("no-balance-permission")
    client.force_login(user)
    assert client.get(url).status_code == 403
    assert client.post(url, {"proposal": token}).status_code == 403
