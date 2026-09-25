import pytest
from django.urls import reverse
from eve_sde.models import ItemType

from aasov.models import PlannedUpgrade, WorkforceRoute
from aasov.optimizer import apply_proposal as apply_ratting
from aasov.optimizer import propose as propose_ratting
from aasov.services import calculate_project, save_upgrade
from aasov.workforce import apply_proposal, propose

pytestmark = pytest.mark.django_db


def add(world, system, status, **kwargs):
    upgrade = world.upgrade(**kwargs)
    return save_upgrade(world.project.pk, system.pk, upgrade, status)


def test_two_scenarios_generation_fuel_inventory_and_offline(world):
    node = world.system("A", power=1000, workforce=1000)
    fuel = ItemType.objects.create(id=9999, name="Fuel")
    add(world, node, "online", power_allocation=100, workforce_allocation=100)
    add(
        world,
        node,
        "temporary",
        power_allocation=200,
        workforce_production=300,
        fuel_item_type=fuel,
        hourly_upkeep=20,
        startup_cost=50,
    )
    add(
        world,
        node,
        "planned",
        power_allocation=400,
        workforce_allocation=200,
        fuel_item_type=fuel,
        hourly_upkeep=30,
        startup_cost=60,
    )
    add(world, node, "offline", power_allocation=9000, workforce_allocation=9000)
    data = calculate_project(world.project)
    b = data["budgets"][0]
    assert (b.power_left, b.workforce_left) == (500, 700)
    assert (b.current.power_left, b.current.workforce_left) == (700, 1200)
    assert b.current.power_percent == 70
    assert b.current.workforce_percent == 92
    assert not b.warnings
    assert data["fuel_totals"] == [{"name": "Fuel", "hourly": 30, "startup": 60}]
    assert data["current_fuel_totals"] == [{"name": "Fuel", "hourly": 20, "startup": 0}]
    assert sum(i["all"] for i in data["inventory"]) == 4
    assert sum(i["planned"] for i in data["inventory"]) == 1


def test_replacement_tiers_exclusive_within_each_scenario(world):
    node = world.system("A")
    add(world, node, "temporary", name="Major Threat 2", power_allocation=200)
    add(world, node, "planned", name="Major Threat 3", power_allocation=300)
    assert not calculate_project(world.project)["budgets"][0].warnings
    add(world, node, "online", name="Major Threat 1", power_allocation=100)
    warnings = calculate_project(world.project)["budgets"][0].warnings
    assert any("Current and planned: Conflicting" in w for w in warnings)


def test_current_deficits_flagged_when_future_fits(world):
    node = world.system("A", power=1000, workforce=1000)
    add(world, node, "temporary", power_allocation=1500, workforce_allocation=1300)
    b = calculate_project(world.project)["budgets"][0]
    assert (b.power_left, b.workforce_left) == (1000, 1000)
    assert (b.current.power_left, b.current.workforce_left) == (-500, -300)
    assert b.current.power_percent == b.current.workforce_percent == 0
    assert b.warnings == ["Current: Power deficit: 500.", "Current: Workforce deficit: 300."]


def test_balance_protects_temporary_donor_and_covers_current_deficit(world):
    donor, receiver = [world.system(n, workforce=100) for n in ("Donor", "Receiver")]
    world.connect(donor, receiver)
    add(world, donor, "temporary", workforce_allocation=80)
    add(world, receiver, "temporary", workforce_allocation=150)
    proposal, preview = propose(world.project)
    assert preview["covered"] == 20 and preview["remaining"] == 30
    apply_proposal(world.project.pk, proposal)
    assert WorkforceRoute.objects.get().amount == 20
    b = {b.system.pk: b for b in calculate_project(world.project)["budgets"]}
    assert b[donor.pk].current.workforce_left == 0
    assert b[receiver.pk].current.workforce_left == -30
    assert b[receiver.pk].workforce_left == 120


def test_ratting_keeps_temporary_tier_and_reserves_current_donor_workforce(world):
    donor = world.system("Donor", power=500, workforce=100)
    target = world.system("Target", power=1000, workforce=100)
    world.connect(donor, target)
    add(world, donor, "temporary", power_allocation=500, workforce_allocation=90)
    temporary = world.upgrade(name="Major Threat 2", power_allocation=600, workforce_allocation=100)
    PlannedUpgrade.objects.create(system=target, upgrade=temporary, status="temporary")
    high = world.upgrade(name="Major Threat 3", power_allocation=1000, workforce_allocation=150)
    low = world.upgrade(name="Minor Threat 1", power_allocation=1000, workforce_allocation=100)
    proposal = propose_ratting(world.project, target.solar_system.constellation_id)
    assert high.pk not in proposal["selected"][str(target.pk)]
    assert temporary.pk not in proposal["selected"][str(target.pk)]
    assert low.pk in proposal["selected"][str(target.pk)]
    apply_ratting(world.project.pk, proposal)
    assert target.upgrades.get(upgrade=temporary).status == "temporary"
    assert all(not b.warnings for b in calculate_project(world.project)["budgets"])


def test_temporary_form_remembered_status_board_and_map(client, editor, world):
    node = world.system("A")
    upgrade = world.upgrade(name="Advanced Logistics Network", power_allocation=1200)
    client.force_login(editor)
    ajax = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
    url = reverse("aasov:upgrade_add", args=[world.project.pk, node.pk])
    response = client.post(url, {"upgrade": upgrade.pk, "status": "temporary"}, **ajax)
    assert response.status_code == 200 and response.json()["saved"]
    html = response.json()["board"]
    assert "Temporary" in html and "Current left" in html and "Planned left" in html
    assert "Current: Power deficit: 200." in html
    assert client.session["aasov_upgrade_status"] == "temporary"
    assert 'value="temporary" selected' in client.get(url, **ajax).json()["html"]
    payload = client.get(reverse("aasov:map_data", args=[world.project.pk])).json()
    item = payload["nodes"][0]
    assert item["logistics"] == "temporary"
    assert item["power"]["left"] == 1000 and item["power"]["current_left"] == -200
