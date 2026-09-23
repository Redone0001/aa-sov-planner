import pytest
from django.core.exceptions import ValidationError
from eve_sde.models import ItemType, PlanetResource, Stargate, StarResource

from aasov.models import PlannedUpgrade, WorkforceRoute
from aasov.services import calculate_project, edit_system, save_route, save_upgrade, sync_project


def budgets(world):
    return {b.system.pk: b for b in calculate_project(world.project)["budgets"]}


def test_sde_resource_aggregation_and_upgrade_states(world):
    system = world.system("Alpha")
    planned = world.upgrade("Planned", power_allocation=400, workforce_allocation=1000)
    online = world.upgrade("Online", power_allocation=900, workforce_allocation=12000)
    offline = world.upgrade("Offline", power_allocation=9999, workforce_allocation=99999)
    save_upgrade(world.project.pk, system.pk, planned, "planned")
    save_upgrade(world.project.pk, system.pk, online, "online")
    save_upgrade(world.project.pk, system.pk, offline, "offline")
    b = budgets(world)[system.pk]
    assert (b.initial_power, b.initial_workforce) == (1000, 10000)
    assert (b.power_left, b.workforce_left) == (-300, -3000)
    assert b.power_percent == b.workforce_percent == 0
    assert len(b.warnings) == 2


def test_conversion_fuel_and_local_only_generation(world):
    a = world.system("A", "export")
    b = world.system("B", "import")
    world.connect(a, b)
    fuel = ItemType.objects.create(id=9999, name="Fuel")
    generator = world.upgrade(
        power_allocation=300,
        workforce_production=5000,
        fuel_item_type=fuel,
        hourly_upkeep=20,
        startup_cost=60,
    )
    save_upgrade(world.project.pk, a.pk, generator, "planned")
    save_route(world.project.pk, a, b, 12000)
    result = calculate_project(world.project)
    data = {x.system.pk: x for x in result["budgets"]}
    assert data[a.pk].workforce_left == 3000
    assert any("cannot be exported" in w for w in data[a.pk].warnings)
    assert data[b.pk].workforce_left == 22000
    assert result["fuel_totals"] == [{"hourly": 20, "startup": 60, "name": "Fuel"}]
    p = a.upgrades.get()
    save_upgrade(world.project.pk, a.pk, generator, "online", p.pk)
    assert calculate_project(world.project)["fuel_totals"][0]["startup"] == 0
    save_upgrade(world.project.pk, a.pk, generator, "offline", p.pk)
    assert calculate_project(world.project)["fuel_totals"] == []


def test_transit_does_not_consume_imports_locally(world):
    a = world.system("A", "export")
    t = world.system("Transit")
    b = world.system("B", "import")
    world.connect(a, t)
    world.connect(t, b)
    save_route(world.project.pk, a, b, 4000)
    result = calculate_project(world.project)
    data = {x.system.pk: x for x in result["budgets"]}
    assert data[a.pk].workforce_left == 6000
    assert data[b.pk].workforce_left == 14000
    assert data[t.pk].workforce_left == 10000
    assert data[t.pk].transiting == 4000
    assert [s.pk for s in result["routes"][0]["path"]] == [a.pk, t.pk, b.pk]
    assert sum(x.workforce_left for x in data.values()) == 30000


def test_three_imports_one_export_and_positive_amount(world):
    dest = world.system("Destination", "import")
    sources = [world.system(str(i), "export") for i in range(4)]
    for source in sources:
        world.connect(source, dest)
    for source in sources[:3]:
        save_route(world.project.pk, source, dest, 100)
    with pytest.raises(ValidationError, match="at most three"):
        save_route(world.project.pk, sources[3], dest, 100)
    with pytest.raises(ValidationError):
        save_route(world.project.pk, sources[0], dest, 100)
    route = WorkforceRoute.objects.get(source=sources[0])
    save_route(world.project.pk, sources[0], dest, 200, route.pk)
    with pytest.raises(ValidationError):
        save_route(world.project.pk, sources[0], dest, 0, route.pk)
    with pytest.raises(ValidationError):
        save_route(world.project.pk, sources[0], dest, -1, route.pk)


def test_missing_path_and_wrong_modes_rejected(world):
    a = world.system("A", "export")
    b = world.system("B", "import")
    with pytest.raises(ValidationError, match="No stargate path"):
        save_route(world.project.pk, a, b, 100)
    middle = world.system("Non-transit", "export")
    world.connect(a, middle)
    world.connect(middle, b)
    with pytest.raises(ValidationError, match="No stargate path"):
        save_route(world.project.pk, a, b, 100)
    with pytest.raises(ValidationError, match="source system"):
        save_route(world.project.pk, b, a, 100)


def test_breaking_mode_change_rejected_and_alternative_path_used(world):
    a = world.system("A", "export")
    t = world.system("Transit")
    b = world.system("B", "import")
    world.connect(a, t)
    world.connect(t, b)
    save_route(world.project.pk, a, b, 100)
    with pytest.raises(ValidationError, match="break an existing route"):
        edit_system(world.project.pk, t.pk, "export")
    t.refresh_from_db()
    assert t.mode == "transit"
    with pytest.raises(ValidationError):
        edit_system(world.project.pk, a.pk, "import")
    alternate = world.system("Alternate")
    world.connect(a, alternate)
    world.connect(alternate, b)
    edit_system(world.project.pk, t.pk, "export")
    result = calculate_project(world.project)
    assert result["routes"][0]["path"][1].pk == alternate.pk


def test_sde_changed_route_flagged_and_not_counted(world):
    a = world.system("A", "export")
    b = world.system("B", "import")
    world.connect(a, b)
    save_route(world.project.pk, a, b, 100)
    Stargate.objects.all().delete()
    result = calculate_project(world.project)
    assert not result["routes"][0]["valid"]
    assert all(x.imported == x.exported == 0 and x.warnings for x in result["budgets"])


def test_missing_sde_resources_are_not_silently_zero(world):
    a = world.system("A")
    StarResource.objects.all().delete()
    assert "incomplete" in budgets(world)[a.pk].warnings[0]
    PlanetResource.objects.all().delete()
    assert budgets(world)[a.pk].initial_power == 0
    assert budgets(world)[a.pk].warnings


def test_mutual_exclusion_warns_and_offline_does_not_conflict(world):
    a = world.system("A")
    first = world.upgrade("Tier 1", mutually_exclusive_group="mining")
    second = world.upgrade("Tier 2", mutually_exclusive_group="mining")
    save_upgrade(world.project.pk, a.pk, first, "online")
    p = save_upgrade(world.project.pk, a.pk, second, "planned")
    assert "Conflicting" in budgets(world)[a.pk].warnings[0]
    save_upgrade(world.project.pk, a.pk, second, "offline", p.pk)
    assert not budgets(world)[a.pk].warnings


def test_project_union_deduplicates_and_preserves_upgrades(world):
    a, b = world.system("A"), world.system("B")
    npc = world.system("NPC", faction_id_raw=500001)
    # Begin with only raw SDE systems, as in a fresh admin project.
    world.project.systems.all().delete()
    world.project.regions.add(world.region)
    world.project.selected_systems.add(a.solar_system, npc.solar_system)
    sync_project(world.project)
    assert world.project.systems.count() == 2
    assert set(world.project.systems.values_list("solar_system_id", flat=True)) == {
        a.solar_system_id,
        b.solar_system_id,
    }
    assert not PlannedUpgrade.objects.exists()
    a = world.project.systems.get(solar_system=a.solar_system)
    save_upgrade(world.project.pk, a.pk, world.upgrade(), "planned")
    sync_project(world.project)
    world.project.regions.clear()
    sync_project(world.project)
    assert world.project.systems.count() == 2
    assert a.upgrades.count() == 1


def test_duplicate_upgrade_rejected(world):
    a = world.system("A")
    upgrade = world.upgrade()
    save_upgrade(world.project.pk, a.pk, upgrade, "planned")
    with pytest.raises(ValidationError):
        save_upgrade(world.project.pk, a.pk, upgrade, "online")


def test_resource_and_gate_reads_are_batched(world, django_assert_num_queries):
    for i in range(20):
        world.system(f"S{i}")
    with django_assert_num_queries(7):
        calculate_project(world.project)


@pytest.mark.parametrize("tiers", [("II", "III"), ("2", "3")])
def test_same_family_tiers_conflict_without_sde_group(world, tiers):
    a = world.system("A")
    first = world.upgrade(f"Major Threat Detection Array {tiers[0]}")
    second = world.upgrade(f"Major Threat Detection Array {tiers[1]}")
    minor = world.upgrade("Minor Threat Detection Array III")
    save_upgrade(world.project.pk, a.pk, first, "online")
    save_upgrade(world.project.pk, a.pk, minor, "planned")
    assert not budgets(world)[a.pk].warnings
    planned = save_upgrade(world.project.pk, a.pk, second, "planned")
    assert any("Conflicting" in w for w in budgets(world)[a.pk].warnings)
    save_upgrade(world.project.pk, a.pk, second, "offline", planned.pk)
    assert not budgets(world)[a.pk].warnings
