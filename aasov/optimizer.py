"""Constellation ratting optimisation with gate paths and natural-workforce routing."""

import hashlib
import json
import re
import time
from collections import Counter, defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction
from eve_sde.models import SovereigntyUpgrade
from ortools.sat.python import cp_model

from . import sde
from .models import PlannedUpgrade, Project, WorkforceRoute
from .services import calculate_project, find_route, project_graph, touch

TIERS = {"I": 1, "II": 2, "III": 3, "1": 1, "2": 2, "3": 3}


def ratting_type(upgrade):
    name = getattr(upgrade.item_type, "name_en", None) or upgrade.item_type.name
    match = re.fullmatch(
        r"(Major|Minor) Threat(?: Detection Array)? (III|II|I|[123])", name.strip(), re.I
    )
    if match:
        return match[1].lower(), TIERS[match[2].upper()]
    return None


def upgrade_data(upgrade):
    return [
        upgrade.pk,
        upgrade.item_type.name,
        getattr(upgrade.item_type, "name_en", None),
        upgrade.power_allocation,
        upgrade.workforce_allocation,
        upgrade.power_production,
        upgrade.workforce_production,
        upgrade.mutually_exclusive_group,
    ]


def inputs(project, constellation_id):
    nodes, graph = project_graph(project.pk)
    scope = {
        pk: node
        for pk, node in nodes.items()
        if node.solar_system.constellation_id == constellation_id
    }
    if not scope:
        raise ValidationError("This constellation has no systems in this plan.")
    if len(scope) > 40:
        raise ValidationError("Optimisation supports up to 40 planned systems per constellation.")
    data = calculate_project(project)
    budgets = {b.system.pk: b for b in data["budgets"]}
    upgrades = list(SovereigntyUpgrade.objects.select_related("item_type").order_by("pk"))
    candidates = [u for u in upgrades if ratting_type(u)]
    if not candidates:
        raise ValidationError(
            "No Major/Minor Threat upgrades found. Load the sovereignty SDE first."
        )
    if any(u.power_allocation is None or u.workforce_allocation is None for u in candidates):
        raise ValidationError("Ratting upgrade resource costs are incomplete in the SDE.")
    raw = {
        "constellation": constellation_id,
        "systems": [[pk, n.solar_system_id, n.mode] for pk, n in sorted(nodes.items())],
        "gates": [[pk, sorted(edges)] for pk, edges in sorted(graph.items())],
        "budgets": [
            [
                pk,
                b.initial_power,
                b.initial_workforce,
                b.warnings,
                [[p.pk, p.status, upgrade_data(p.upgrade)] for p in b.upgrades],
            ]
            for pk, b in sorted(budgets.items())
        ],
        "routes": [
            [r["route"].pk, r["route"].source_id, r["route"].destination_id, r["route"].amount]
            for r in data["routes"]
        ],
        "candidates": [upgrade_data(u) for u in candidates],
    }
    fingerprint = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    return nodes, graph, scope, budgets, candidates, data["routes"], fingerprint


def propose(project, constellation_id, time_limit=10):
    nodes, graph, scope, budgets, candidates, routes, fingerprint = inputs(
        project, constellation_id
    )
    for pk in scope:
        if any("incomplete" in w for w in budgets[pk].warnings):
            raise ValidationError("Resource data is incomplete. Reload the SDE before optimising.")
    model = cp_model.CpModel()
    modes = {
        pk: {mode: model.new_bool_var(f"{pk}_{mode}") for mode in ("import", "export", "transit")}
        for pk in scope
    }
    for options in modes.values():
        model.add(sum(options.values()) == 1)
    fixed_import = defaultdict(int)
    fixed_export = defaultdict(int)
    fixed_sources = defaultdict(int)
    removable = []
    for row in routes:
        route = row["route"]
        if (
            route.source_id in scope
            and route.destination_id in scope
            and all(n.pk in scope for n in row["path"])
        ):
            removable.append(route.pk)
            continue
        touched = {route.source_id, route.destination_id} | {n.pk for n in row["path"]}
        if touched & scope.keys():
            if not row["valid"]:
                raise ValidationError(
                    "Repair invalid routes crossing this constellation before optimising."
                )
            for pk in touched & scope.keys():
                model.add(modes[pk][nodes[pk].mode] == 1)
        fixed_import[route.destination_id] += route.amount
        fixed_sources[route.destination_id] += 1
        fixed_export[route.source_id] += route.amount

    chosen = {}
    available = {}
    for pk in scope:
        b = budgets[pk]
        other = [
            p.upgrade for p in b.upgrades if p.status != "offline" and not ratting_type(p.upgrade)
        ]
        preserved_groups = Counter()
        for u in other:
            if u.mutually_exclusive_group:
                preserved_groups[("sde", u.mutually_exclusive_group)] += 1
            family = sde.upgrade_family(u)
            if family:
                preserved_groups[("family", family)] += 1
        if any(count > 1 for count in preserved_groups.values()) or (
            any(u.power_production for u in other) and any(u.workforce_production for u in other)
        ):
            raise ValidationError(
                f"{b.system} has conflicting non-ratting upgrades. Resolve them before optimising."
            )
        power = b.initial_power + sum(
            (u.power_production or 0) - (u.power_allocation or 0) for u in other
        )
        workforce = (
            b.initial_workforce
            + sum((u.workforce_production or 0) - (u.workforce_allocation or 0) for u in other)
            + fixed_import[pk]
            - fixed_export[pk]
        )
        available[pk] = (power, workforce)
        groups = defaultdict(list)
        for u in candidates:
            var = chosen[pk, u.pk] = model.new_bool_var(f"upgrade_{pk}_{u.pk}")
            groups[("family", ratting_type(u)[0])].append(var)
            if u.mutually_exclusive_group:
                groups[("sde", u.mutually_exclusive_group)].append(var)
        for key, variables in groups.items():
            occupied = sum(
                1 for u in other if key[0] == "sde" and u.mutually_exclusive_group == key[1]
            )
            model.add(sum(variables) + occupied <= 1)
        model.add(
            sum(
                chosen[pk, u.pk] * ((u.power_allocation or 0) - (u.power_production or 0))
                for u in candidates
            )
            <= power
        )

    # Each selected source/destination pair gets one acyclic gate path. Every
    # intermediate node must be Transit; no flow can pass through an importer/exporter.
    edges = [(a, b) for a in scope for b in sorted(graph.get(a, ())) if b in scope]
    route_vars, amounts = {}, {}
    for source in scope:
        cap = min(budgets[source].initial_workforce, max(0, available[source][1]), 2147483647)
        if not cap or fixed_export[source]:
            continue
        for destination in scope:
            if (
                source == destination
                or fixed_export[destination]
                or fixed_sources[destination] >= 3
            ):
                continue
            # Fast connectivity check, allowing all potential intermediate modes.
            reachable = {source}
            todo = [source]
            while todo:
                for neighbor in graph.get(todo.pop(), ()):
                    if neighbor in scope and neighbor not in reachable:
                        reachable.add(neighbor)
                        todo.append(neighbor)
            if destination not in reachable:
                continue
            active = route_vars[source, destination] = model.new_bool_var(
                f"route_{source}_{destination}"
            )
            amount = amounts[source, destination] = model.new_int_var(
                0, cap, f"amount_{source}_{destination}"
            )
            model.add(amount >= active)
            model.add(amount <= cap * active)
            model.add(active <= modes[source]["export"])
            model.add(active <= modes[destination]["import"])
            path_edges = {
                (a, b): model.new_bool_var(f"path_{source}_{destination}_{a}_{b}")
                for a, b in edges
                if b != source and a != destination
            }
            order = {
                pk: model.new_int_var(0, len(scope) - 1, f"order_{source}_{destination}_{pk}")
                for pk in scope
            }
            for (a, b), used in path_edges.items():
                model.add(used <= active)
                model.add(order[b] > order[a]).only_enforce_if(used)
            for pk in scope:
                incoming = sum(v for (a, b), v in path_edges.items() if b == pk)
                outgoing = sum(v for (a, b), v in path_edges.items() if a == pk)
                if pk == source:
                    model.add(outgoing == active)
                elif pk == destination:
                    model.add(incoming == active)
                else:
                    model.add(incoming == outgoing)
                    model.add(incoming <= modes[pk]["transit"])
    for pk in scope:
        model.add(
            sum(v for (s, d), v in route_vars.items() if s == pk) + bool(fixed_export[pk]) <= 1
        )
        model.add(sum(v for (s, d), v in route_vars.items() if d == pk) + fixed_sources[pk] <= 3)
        imports = sum(v for (s, d), v in amounts.items() if d == pk)
        exports = sum(v for (s, d), v in amounts.items() if s == pk)
        model.add(exports + fixed_export[pk] <= budgets[pk].initial_workforce)
        model.add(
            sum(
                chosen[pk, u.pk] * ((u.workforce_allocation or 0) - (u.workforce_production or 0))
                for u in candidates
            )
            <= available[pk][1] + imports - exports
        )

    # Lexicographic counts: III, II, I, then Major III, Major II, Major I.
    # The base exceeds the number of possible active upgrades, so lower levels
    # can never outweigh one higher-level upgrade.
    base = 2 * len(scope) + 1
    score = sum(
        var
        * (
            base ** (ratting_type(u)[1] + 2)
            + (base ** (ratting_type(u)[1] - 1) if ratting_type(u)[0] == "major" else 0)
        )
        for pk in scope
        for u in candidates
        for var in [chosen[pk, u.pk]]
    )
    model.maximize(score)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 1
    start = time.monotonic()
    status = solver.solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if status == cp_model.INFEASIBLE:
            raise ValidationError(
                "No feasible plan fits the preserved non-ratting upgrades and boundary routes. Resolve their deficits first."
            )
        raise ValidationError(
            "The optimiser did not find a feasible plan within its time limit. No changes were made."
        )
    optimal = status == cp_model.OPTIMAL
    result = solver
    # With the ratting score fixed, prefer fewer routes, then less transferred
    # workforce and fewer mode changes. Retain the first solution on timeout.
    remaining = time_limit - (time.monotonic() - start)
    if optimal and remaining > 0.1:
        model.add(score == solver.value(score))
        mode_changes = sum(1 - modes[pk][nodes[pk].mode] for pk in scope)
        flow_bound = sum(budgets[pk].initial_workforce for pk in scope) + 1
        model.minimize(
            sum(route_vars.values()) * flow_bound * (len(scope) + 1)
            + sum(amounts.values()) * (len(scope) + 1)
            + mode_changes
        )
        tidy = cp_model.CpSolver()
        tidy.parameters.max_time_in_seconds = remaining
        tidy.parameters.num_search_workers = 1
        if tidy.solve(model) in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            result = tidy
    selected = {
        str(pk): [u.pk for u in candidates if result.value(chosen[pk, u.pk])] for pk in scope
    }
    new_modes = {
        str(pk): next(mode for mode, var in options.items() if result.value(var))
        for pk, options in modes.items()
    }
    new_routes = [
        [s, d, result.value(amounts[s, d])]
        for (s, d), var in route_vars.items()
        if result.value(var)
    ]
    return {
        "project": project.pk,
        "constellation": constellation_id,
        "fingerprint": fingerprint,
        "selected": selected,
        "modes": new_modes,
        "routes": new_routes,
        "remove_routes": removable,
        "optimal": optimal,
    }


def preview_rows(project, proposal):
    nodes, graph, scope, budgets, candidates, routes, _ = inputs(project, proposal["constellation"])
    catalog = {u.pk: u for u in candidates}
    rows = []
    for pk, node in scope.items():
        old = [p for p in budgets[pk].upgrades if ratting_type(p.upgrade) and p.status != "offline"]
        selected = [catalog[uid] for uid in proposal["selected"][str(pk)]]
        kept = [
            p.upgrade
            for p in budgets[pk].upgrades
            if not ratting_type(p.upgrade) and p.status != "offline"
        ]
        planned = kept + selected
        imported = sum(amount for s, d, amount in proposal["routes"] if d == pk)
        exported = sum(amount for s, d, amount in proposal["routes"] if s == pk)
        for row in routes:
            r = row["route"]
            if r.pk not in proposal["remove_routes"] and row["valid"]:
                imported += r.amount if r.destination_id == pk else 0
                exported += r.amount if r.source_id == pk else 0
        rows.append(
            {
                "system": node,
                "old": old,
                "upgrades": selected,
                "mode": proposal["modes"][str(pk)],
                "power": budgets[pk].initial_power
                + sum((u.power_production or 0) - (u.power_allocation or 0) for u in planned),
                "workforce": budgets[pk].initial_workforce
                + sum(
                    (u.workforce_production or 0) - (u.workforce_allocation or 0) for u in planned
                )
                + imported
                - exported,
            }
        )
        node.mode = proposal["modes"][str(pk)]
    route_rows = []
    for s, d, amount in proposal["routes"]:
        path = find_route(
            s, d, scope, {pk: edges & scope.keys() for pk, edges in graph.items() if pk in scope}
        )
        route_rows.append(
            {
                "source": nodes[s],
                "destination": nodes[d],
                "amount": amount,
                "path": [nodes[pk] for pk in path],
            }
        )
    return {
        "optimisation_rows": rows,
        "optimisation_routes": route_rows,
        "removed_routes": [row for row in routes if row["route"].pk in proposal["remove_routes"]],
        "optimal": proposal["optimal"],
    }


@transaction.atomic
def apply_proposal(project_id, proposal):
    project = Project.objects.select_for_update().get(pk=project_id)
    if proposal["project"] != project.pk:
        raise ValidationError("This preview belongs to another project.")
    _, _, scope, _, _, old_routes, fingerprint = inputs(project, proposal["constellation"])
    previously_invalid = {row["route"].pk for row in old_routes if not row["valid"]}
    if fingerprint != proposal["fingerprint"]:
        raise ValidationError(
            "The plan or SDE changed since this preview. Close it and run Best ratting again."
        )
    for pk, node in scope.items():
        selected = set(proposal["selected"][str(pk)])
        for planned in node.upgrades.select_related("upgrade__item_type"):
            if ratting_type(planned.upgrade):
                planned.status = (
                    (planned.status if planned.status == "online" else "planned")
                    if planned.upgrade_id in selected
                    else "offline"
                )
                planned.save(update_fields=["status"])
                selected.discard(planned.upgrade_id)
        for uid in selected:
            PlannedUpgrade.objects.create(system=node, upgrade_id=uid, status="planned")
        node.mode = proposal["modes"][str(pk)]
        node.save(update_fields=["mode"])
    WorkforceRoute.objects.filter(
        pk__in=proposal["remove_routes"], source__project=project
    ).delete()
    for source, destination, amount in proposal["routes"]:
        route = WorkforceRoute(source=scope[source], destination=scope[destination], amount=amount)
        route.full_clean()
        route.save()
    data = calculate_project(project)
    if any(b.warnings for b in data["budgets"] if b.system.pk in scope):
        raise ValidationError(
            "The resulting plan has resource or route conflicts. No changes were applied."
        )
    if any(
        not row["valid"] and row["route"].pk not in previously_invalid for row in data["routes"]
    ):
        raise ValidationError("A route is invalid. Repair it before applying optimisation.")
    touch(project)
