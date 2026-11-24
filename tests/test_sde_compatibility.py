"""Exercise the planner against data imported by each supported SDE loader."""

import json
from importlib.metadata import version

import pytest
from eve_sde.models import (
    ItemType,
    Planet,
    PlanetResource,
    SovereigntyUpgrade,
    Star,
    Stargate,
    StarResource,
)
from packaging.version import Version

from aasov.map_data import system_node
from aasov.models import PlannedUpgrade
from aasov.sde import gate_graph, resources_for
from aasov.services import calculate_project, save_route, save_upgrade

SDE_VERSION = Version(version("django-eveonline-sde"))


@pytest.fixture(
    params=[
        "zip",
        pytest.param(
            "rest",
            marks=pytest.mark.skipif(
                SDE_VERSION < Version("2.0.0"), reason="REST imports require SDE 2.0+"
            ),
        ),
    ]
)
def imported_plan(world, tmp_path, request):
    source = world.system("Source", "export")
    destination = world.system("Destination", "import")
    world.connect(source, destination)
    gate = Stargate.objects.get()
    fuel = ItemType.objects.create(id=9999, name="Test fuel")
    upgrade = world.upgrade("Imported upgrade")
    # Start with parent objects only, as on a first SDE import.
    PlanetResource.objects.all().delete()
    StarResource.objects.all().delete()
    SovereigntyUpgrade.objects.filter(pk=upgrade.pk).delete()
    world.project.selected_systems.add(source.solar_system, destination.solar_system)
    (tmp_path / "_sde.jsonl").write_text(json.dumps({"buildNumber": 1000}), encoding="utf-8")

    def write_table(filename, key, rows):
        if request.param == "rest":
            rows = [{key: row["_key"], **{k: v for k, v in row.items() if k != "_key"}} for row in rows]
            # Synthetic schemas use CCP's REST key names; the real SDE loader
            # must translate them back to the model import configuration.
            schema = {"x-sde-key": key, "type": "object", "properties": {key: {"type": "integer"}}}
            (tmp_path / f"{filename}.schema.json").write_text(json.dumps(schema), encoding="utf-8")
        (tmp_path / f"{filename}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )

    def load(power=740, allocation=250, include_upgrade=True):
        resources = []
        for system, star_power, workforce in [(source, power, 5730), (destination, 900, 8000)]:
            star = Star.objects.get(solar_system=system.solar_system)
            planet = Planet.objects.get(solar_system=system.solar_system)
            resources.extend(
                [
                    {"_key": star.pk, "power": star_power, "workforce": 0},
                    {"_key": planet.pk, "power": 0, "workforce": workforce},
                ]
            )
        write_table("planetResources", "celestialID", resources)
        write_table(
            "sovereigntyUpgrades",
            "typeID",
            [
                {
                    "_key": upgrade.pk,
                    "fuel": {"type_id": fuel.pk, "hourly_upkeep": 205, "startup_cost": 62000},
                    "power_allocation": allocation,
                    "workforce_allocation": 1500,
                    "mutually_exclusive_group": "test-infrastructure",
                }
            ]
            if include_upgrade
            else [],
        )
        write_table(
            "mapStargates",
            "stargateID",
            [
                {
                    "_key": gate.pk,
                    "solarSystemID": source.solar_system_id,
                    "destination": {"solarSystemID": destination.solar_system_id},
                }
            ],
        )
        for model in [PlanetResource, StarResource, SovereigntyUpgrade, Stargate]:
            model.load_from_sde(str(tmp_path))

    load()
    upgrade.refresh_from_db()
    planned = save_upgrade(world.project.pk, source.pk, upgrade, "planned")
    return world.project, source, destination, planned, gate.pk, load


def test_imported_resources_costs_fuel_and_map(imported_plan):
    project, source, destination, planned, gate_id, load = imported_plan
    resource = resources_for([source.solar_system_id])[source.solar_system_id]
    assert (resource.power, resource.workforce, resource.complete) == (740, 5730, True)
    result = calculate_project(project)
    budget = next(b for b in result["budgets"] if b.system.pk == source.pk)
    assert (budget.power_left, budget.workforce_left) == (490, 4230)
    assert result["fuel_totals"] == [{"name": "Test fuel", "hourly": 205, "startup": 62000}]
    assert planned.upgrade.mutually_exclusive_group == "test-infrastructure"
    assert system_node(source.solar_system, None)["name"] == "Source"


@pytest.mark.xfail(
    SDE_VERSION < Version("1.0.0"),
    reason="SDE 0.2.0's updater passes primary-key fields to bulk_update when resources change",
    raises=ValueError,
    strict=True,
)
def test_reload_preserves_plans_and_routes_and_updates_budgets(imported_plan):
    project, source, destination, planned, gate_id, load = imported_plan
    route = save_route(project.pk, source, destination, 2000)
    original_upgrade_id = planned.upgrade_id
    load(power=900, allocation=300)
    planned.refresh_from_db()
    assert planned.upgrade_id == original_upgrade_id
    assert project.selected_systems.filter(pk=source.solar_system_id).exists()
    assert Stargate.objects.filter(pk=gate_id).exists()
    assert destination.solar_system_id in gate_graph(
        [source.solar_system_id, destination.solar_system_id]
    )[source.solar_system_id]
    result = calculate_project(project)
    budget = next(b for b in result["budgets"] if b.system.pk == source.pk)
    assert (budget.initial_power, budget.power_left, budget.workforce_left) == (900, 600, 2230)
    assert result["routes"][0]["route"].pk == route.pk
    assert result["routes"][0]["valid"]
    assert [s.pk for s in result["routes"][0]["path"]] == [source.pk, destination.pk]


@pytest.mark.skipif(SDE_VERSION < Version("1.0.0"), reason="Deferred cleanup requires SDE 1.0+")
def test_reload_keeps_obsolete_upgrade_protected_by_saved_plan(imported_plan, caplog):
    from eve_sde.sde_tasks import delete_removed_rows

    project, source, destination, planned, gate_id, load = imported_plan
    load(include_upgrade=False)
    with caplog.at_level("ERROR", logger="eve_sde.sde_tasks"):
        delete_removed_rows()
    assert "SovereigntyUpgrade - Failed to remove rows" in caplog.text
    assert PlannedUpgrade.objects.get(pk=planned.pk).upgrade_id == planned.upgrade_id
    assert SovereigntyUpgrade.objects.filter(pk=planned.upgrade_id).exists()
