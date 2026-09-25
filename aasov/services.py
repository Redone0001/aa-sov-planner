"""Planning calculations and serialized mutations.

Routes carry workforce from origin to final destination. Transit nodes never consume
that flow. Locking the project serializes concurrent edits and the three-import cap.
"""

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from . import sde
from .models import PlannedSystem, PlannedUpgrade, Project, WorkforceRoute


def sync_project(project):
    """Add selected systems/regions, preserving existing plans on later admin saves."""
    with transaction.atomic():
        Project.objects.select_for_update().get(pk=project.pk)
        selected = (
            sde.eligible_systems()
            .filter(
                Q(pk__in=project.selected_systems.values("pk"))
                | Q(constellation__region__in=project.regions.values("pk"))
            )
            .exclude(pk__in=project.excluded_systems.values("pk"))
            .values_list("pk", flat=True)
        )
        existing = set(project.systems.values_list("solar_system_id", flat=True))
        new_ids = [pk for pk in selected if pk not in existing]
        PlannedSystem.objects.bulk_create(
            [PlannedSystem(project=project, solar_system_id=pk) for pk in new_ids]
        )

        if new_ids:
            from .tasks import queue_ownership_snapshot

            transaction.on_commit(
                lambda: queue_ownership_snapshot(project.pk, new_ids), robust=True
            )


def project_graph(project_id):
    systems = list(
        PlannedSystem.objects.filter(project_id=project_id).select_related(
            "solar_system__constellation"
        )
    )
    nodes = {s.pk: s for s in systems}
    by_eve_id = {s.solar_system_id: s.pk for s in systems}
    gates = sde.gate_graph(by_eve_id)
    graph = {by_eve_id[k]: {by_eve_id[v] for v in values} for k, values in gates.items()}
    return nodes, graph


def find_route(source, destination, nodes, graph):
    """Deterministic shortest stargate path; intermediates must be in Transit mode."""
    if source == destination or source not in nodes or destination not in nodes:
        return None
    queue = deque([source])
    previous = {source: None}
    while queue:
        current = queue.popleft()
        for neighbor in sorted(graph.get(current, ())):
            if neighbor in previous:
                continue
            if neighbor != destination and nodes[neighbor].mode != PlannedSystem.Mode.TRANSIT:
                continue
            previous[neighbor] = current
            if neighbor == destination:
                path = [destination]
                while previous[path[-1]] is not None:
                    path.append(previous[path[-1]])
                return list(reversed(path))
            queue.append(neighbor)
    return None


@dataclass
class Budget:
    system: PlannedSystem
    initial_power: int = 0
    initial_workforce: int = 0
    power_used: int = 0
    workforce_used: int = 0
    power_generated: int = 0
    workforce_generated: int = 0
    imported: int = 0
    exported: int = 0
    transiting: int = 0
    upgrades: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    fuels: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    exports: list = field(default_factory=list)
    transit_routes: list = field(default_factory=list)

    current: "Budget | None" = None

    @property
    def power_left(self):
        return self.initial_power + self.power_generated - self.power_used

    @property
    def workforce_left(self):
        return (
            self.initial_workforce
            + self.workforce_generated
            + self.imported
            - self.exported
            - self.workforce_used
        )

    @property
    def power_percent(self):
        return percentage(self.power_left, self.initial_power + self.power_generated)

    @property
    def workforce_percent(self):
        return percentage(
            self.workforce_left, self.initial_workforce + self.workforce_generated + self.imported
        )

    @property
    def import_percent(self):
        return percentage(
            self.imported, self.initial_workforce + self.workforce_generated + self.imported
        )


def percentage(value, total):
    return max(0, min(100, round(100 * value / total))) if total > 0 else 0


def calculate_project(project):
    nodes, graph = project_graph(project.pk)
    resources = sde.resources_for([s.solar_system_id for s in nodes.values()])
    upgrades = list(
        PlannedUpgrade.objects.filter(system__project=project)
        .select_related("upgrade__item_type", "upgrade__fuel_item_type")
        .order_by("pk")
    )
    routes = list(
        WorkforceRoute.objects.filter(source__project=project)
        .select_related("source__solar_system", "destination__solar_system")
        .order_by("pk")
    )
    planned = _calculate_project(nodes, graph, resources, upgrades, routes, {"online", "planned"})
    current = _calculate_project(nodes, graph, resources, upgrades, routes, {"online", "temporary"})
    current_budgets = {b.system.pk: b for b in current["budgets"]}
    for b in planned["budgets"]:
        b.current = current_budgets[b.system.pk]
        future_warnings = b.warnings
        b.warnings = [
            ("Current and planned: " if w in b.current.warnings else "Planned: ") + w
            for w in future_warnings
        ] + ["Current: " + w for w in b.current.warnings if w not in future_warnings]
    planned["current_fuel_totals"] = current["fuel_totals"]
    planned["warning_count"] = sum(bool(b.warnings) for b in planned["budgets"])
    return planned


def _calculate_project(nodes, graph, resources, upgrades, routes, active_statuses):
    budgets = {}
    fuel_totals = defaultdict(lambda: {"hourly": 0, "startup": 0, "name": ""})
    for pk, system in nodes.items():
        resource = resources[system.solar_system_id]
        b = budgets[pk] = Budget(system, resource.power, resource.workforce)
        if not resource.complete:
            b.warnings.append(
                "SDE resource data is incomplete. Reload the SDE before relying on this budget."
            )
    groups = defaultdict(Counter)
    inventory = {}
    for planned in upgrades:
        b = budgets[planned.system_id]
        b.upgrades.append(planned)
        u = planned.upgrade
        item = inventory.setdefault(
            u.item_type_id, {"name": u.item_type.name, "planned": 0, "all": 0}
        )
        item["all"] += 1
        if planned.status == PlannedUpgrade.Status.PLANNED:
            item["planned"] += 1
        if planned.status not in active_statuses:
            continue
        b.power_used += u.power_allocation or 0
        b.workforce_used += u.workforce_allocation or 0
        b.power_generated += u.power_production or 0
        b.workforce_generated += u.workforce_production or 0
        if u.mutually_exclusive_group:
            groups[planned.system_id][("sde", u.mutually_exclusive_group)] += 1
        family = sde.upgrade_family(u)
        if family:
            groups[planned.system_id][("family", family)] += 1
        if u.fuel_item_type_id:
            fuel = {
                "name": u.fuel_item_type.name,
                "hourly": u.hourly_upkeep or 0,
                "startup": (u.startup_cost or 0)
                if planned.status == PlannedUpgrade.Status.PLANNED
                else 0,
            }
            b.fuels.append(fuel)
            total = fuel_totals[u.fuel_item_type_id]
            total["name"] = fuel["name"]
            total["hourly"] += fuel["hourly"]
            total["startup"] += fuel["startup"]
    route_rows = []
    for route in routes:
        path = find_route(route.source_id, route.destination_id, nodes, graph)
        valid = (
            path is not None
            and route.destination_id in budgets
            and nodes[route.source_id].mode == "export"
            and nodes[route.destination_id].mode == "import"
        )
        if not valid:
            for pk in (route.source_id, route.destination_id):
                if pk in budgets:
                    budgets[pk].warnings.append(
                        "Workforce route is invalid; its allocation is not counted."
                    )
        else:
            budgets[route.source_id].exported += route.amount
            budgets[route.destination_id].imported += route.amount
            for pk in path[1:-1]:
                budgets[pk].transiting += route.amount
        row = {"route": route, "valid": valid, "path": [nodes[pk] for pk in path] if path else []}
        route_rows.append(row)
        budgets[route.source_id].exports.append(row)
        if route.destination_id in budgets:
            budgets[route.destination_id].imports.append(row)
        if valid:
            for pk in path[1:-1]:
                budgets[pk].transit_routes.append(row)
    for pk, b in budgets.items():
        if b.power_left < 0:
            b.warnings.append(f"Power deficit: {-b.power_left:,}.")
        if b.workforce_left < 0:
            b.warnings.append(f"Workforce deficit: {-b.workforce_left:,}.")
        if b.exported > b.initial_workforce:
            b.warnings.append(
                "Export exceeds natural workforce. Upgrade-generated workforce cannot be exported."
            )
        if any(count > 1 for count in groups[pk].values()) or (
            b.power_generated and b.workforce_generated
        ):
            b.warnings.append(
                "Conflicting active upgrades: mutually exclusive upgrades cannot operate together."
            )
    ordered = sorted(
        budgets.values(),
        key=lambda b: (
            b.system.solar_system.constellation.name,
            b.system.solar_system.constellation_id,
            b.system.solar_system.name,
        ),
    )
    constellations = {}
    for b in ordered:
        c = b.system.solar_system.constellation
        constellations.setdefault(c.pk, {"constellation": c, "budgets": []})["budgets"].append(b)
    inventory_rows = sorted(inventory.values(), key=lambda item: item["name"].casefold())
    return {
        "inventory": inventory_rows,
        "inventory_clipboard": {
            scope: "\n".join(
                f"{item['name']}\t{item[scope]}" for item in inventory_rows if item[scope]
            )
            for scope in ("planned", "all")
        },
        "budgets": ordered,
        "constellations": list(constellations.values()),
        "routes": route_rows,
        "fuel_totals": list(fuel_totals.values()),
        "warning_count": sum(bool(b.warnings) for b in budgets.values()),
    }


@transaction.atomic
def edit_system(project_id, system_id, mode):
    project = Project.objects.select_for_update().get(pk=project_id)
    system = project.systems.get(pk=system_id)
    system.mode = mode
    system.full_clean()
    nodes, graph = project_graph(project_id)
    nodes[system.pk] = system
    for route in WorkforceRoute.objects.filter(source__project=project):
        if (
            nodes[route.source_id].mode != "export"
            or nodes[route.destination_id].mode != "import"
            or find_route(route.source_id, route.destination_id, nodes, graph) is None
        ):
            raise ValidationError(
                "This mode change would break an existing route. Remove or reroute it first."
            )
    system.save()
    touch(project)


@transaction.atomic
def save_upgrade(project_id, system_id, upgrade, status, planned_id=None):
    project = Project.objects.select_for_update().get(pk=project_id)
    system = project.systems.get(pk=system_id)
    planned = system.upgrades.get(pk=planned_id) if planned_id else PlannedUpgrade(system=system)
    planned.upgrade = upgrade
    planned.status = status
    planned.full_clean()
    planned.save()
    touch(project)
    return planned


def route_proposal(project_id, source_id, destination_id, route_id=None):
    """Validate endpoint modes and the full transit path without changing the plan."""
    nodes, graph = project_graph(project_id)
    if source_id not in nodes or destination_id not in nodes:
        raise ValidationError("Choose source and destination systems from this project.")
    if source_id == destination_id:
        raise ValidationError("A system cannot export to itself.")
    routes = list(WorkforceRoute.objects.filter(source__project_id=project_id).exclude(pk=route_id))
    if any(r.source_id == source_id for r in routes):
        raise ValidationError(
            "This source already exports to a destination. Edit its existing route instead."
        )
    if sum(r.destination_id == destination_id for r in routes) >= 3:
        raise ValidationError("A system can import from at most three source systems.")
    nodes[source_id].mode = "export"
    nodes[destination_id].mode = "import"
    path = find_route(source_id, destination_id, nodes, graph)
    if path is None:
        raise ValidationError(
            "No valid stargate path. Intermediate systems must be in this project and in Transit mode."
        )
    for r in routes:
        if (
            nodes[r.source_id].mode != "export"
            or nodes[r.destination_id].mode != "import"
            or find_route(r.source_id, r.destination_id, nodes, graph) is None
        ):
            raise ValidationError(
                "Setting these endpoints to Export and Import would break an existing route. Remove or reroute it first."
            )
    return [nodes[pk] for pk in path]


@transaction.atomic
def save_route(project_id, source, destination, amount, route_id=None, *, configure_modes=False):
    project = Project.objects.select_for_update().get(pk=project_id)
    route = (
        WorkforceRoute.objects.get(pk=route_id, source__project=project)
        if route_id
        else WorkforceRoute()
    )
    route.source = project.systems.get(pk=source.pk)
    route.destination = project.systems.get(pk=destination.pk)
    route.amount = amount
    if configure_modes:
        route_proposal(project_id, route.source_id, route.destination_id, route_id)
        route.source.mode = "export"
        route.destination.mode = "import"
        route.source.save(update_fields=["mode"])
        route.destination.save(update_fields=["mode"])
    route.full_clean()
    route.save()
    touch(project)
    return route


@transaction.atomic
def remove_item(project_id, kind, item_id):
    project = Project.objects.select_for_update().get(pk=project_id)
    if kind == "upgrade":
        PlannedUpgrade.objects.get(pk=item_id, system__project=project).delete()
    elif kind == "route":
        WorkforceRoute.objects.get(pk=item_id, source__project=project).delete()
    else:
        raise ValueError("Unknown item type")
    touch(project)


def touch(project):
    Project.objects.filter(pk=project.pk).update(updated_at=timezone.now())


@transaction.atomic
def remove_system(project_id, system_id):
    project = Project.objects.select_for_update().get(pk=project_id)
    system = project.systems.get(pk=system_id)
    project.excluded_systems.add(system.solar_system_id)
    system.delete()
    # Endpoint routes cascade. Routes using this system for transit remain visible
    # with invalid-path warnings unless an alternative transit path exists.
    touch(project)


@transaction.atomic
def install_upgrade(project_id, system_id, upgrade_id):
    project = Project.objects.select_for_update().get(pk=project_id)
    planned = PlannedUpgrade.objects.get(
        pk=upgrade_id, system_id=system_id, system__project=project
    )
    if planned.status == PlannedUpgrade.Status.OFFLINE:
        raise ValidationError(
            "This upgrade is now Offline. Refresh the plan and edit it explicitly to bring it Online."
        )
    if planned.status == PlannedUpgrade.Status.PLANNED:
        planned.status = PlannedUpgrade.Status.ONLINE
        planned.save(update_fields=["status"])
        touch(project)
