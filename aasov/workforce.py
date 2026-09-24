"""Cover workforce deficits using spare natural workforce in adjacent systems."""

import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction
from ortools.sat.python import cp_model

from .models import Project
from .optimizer import upgrade_data
from .services import calculate_project, project_graph, save_route


def inputs(project):
    nodes, graph = project_graph(project.pk)
    data = calculate_project(project)
    budgets = {b.system.pk: b for b in data["budgets"]}
    if any(not row["valid"] for row in data["routes"]):
        raise ValidationError("Repair invalid workforce routes before balancing this plan.")
    if any(
        "SDE resource data is incomplete" in warning
        for b in budgets.values()
        for warning in b.warnings
    ):
        raise ValidationError("Load complete SDE resource data before balancing workforce.")
    raw = {
        "systems": [[pk, n.solar_system_id, n.mode] for pk, n in sorted(nodes.items())],
        "gates": [[pk, sorted(edges)] for pk, edges in sorted(graph.items())],
        "budgets": [
            [
                pk,
                b.initial_workforce,
                b.workforce_left,
                [[u.pk, u.status, upgrade_data(u.upgrade)] for u in b.upgrades],
            ]
            for pk, b in sorted(budgets.items())
        ],
        "routes": [
            [r["route"].pk, r["route"].source_id, r["route"].destination_id, r["route"].amount]
            for r in data["routes"]
        ],
    }
    fingerprint = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    return nodes, graph, budgets, fingerprint


def propose(project):
    nodes, graph, budgets, fingerprint = inputs(project)
    model = cp_model.CpModel()
    edges = []
    for source, b in sorted(budgets.items()):
        spare = min(b.workforce_left, b.initial_workforce - b.exported)
        if spare <= 0 or b.imports or b.transit_routes:
            continue
        existing = b.exports[0]["route"] if b.exports else None
        for destination in sorted(graph.get(source, ())):
            target = budgets[destination]
            if target.workforce_left >= 0 or target.exports or target.transit_routes:
                continue
            if existing and existing.destination_id != destination:
                continue
            if not existing and len(target.imports) >= 3:
                continue
            current = existing.amount if existing else 0
            limit = min(spare, -target.workforce_left, 2147483647 - current)
            if limit <= 0:
                continue
            amount = model.new_int_var(0, limit, f"amount_{source}_{destination}")
            used = model.new_bool_var(f"used_{source}_{destination}")
            model.add(amount <= limit * used)
            model.add(amount >= used)
            edges.append(
                (source, destination, amount, used, existing.pk if existing else None, current)
            )
    for pk, b in budgets.items():
        outgoing = [e for e in edges if e[0] == pk]
        incoming = [e for e in edges if e[1] == pk]
        if outgoing:
            model.add(sum(e[3] for e in outgoing) <= 1)
            model.add(
                sum(e[2] for e in outgoing)
                <= min(b.workforce_left, b.initial_workforce - b.exported)
            )
        if incoming:
            model.add(sum(e[2] for e in incoming) <= -b.workforce_left)
            model.add(sum(e[3] for e in incoming if e[4] is None) <= 3 - len(b.imports))
    routes = []
    optimal = True
    if edges:
        # First maximise covered workforce, then minimise changed routes.
        model.maximize(sum(e[2] for e in edges) * (len(edges) + 1) - sum(e[3] for e in edges))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 10
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 0
        status = solver.solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            raise ValidationError(
                "No balancing preview was found within the time limit. Try a smaller plan."
            )
        optimal = status == cp_model.OPTIMAL
        for source, destination, amount, _, route_id, current in edges:
            extra = solver.value(amount)
            if extra:
                routes.append([source, destination, current + extra, route_id, extra])
    remaining = {pk: b.workforce_left for pk, b in budgets.items()}
    for source, destination, _, _, extra in routes:
        remaining[source] -= extra
        remaining[destination] += extra
    preview = {
        "rows": [
            {"system": nodes[pk], "before": budgets[pk].workforce_left, "after": value}
            for pk, value in remaining.items()
            if value != budgets[pk].workforce_left or value < 0
        ],
        "routes": [
            {
                "source": nodes[s],
                "destination": nodes[d],
                "amount": amount,
                "extra": extra,
                "existing": bool(rid),
            }
            for s, d, amount, rid, extra in routes
        ],
        "remaining": sum(max(0, -v) for v in remaining.values()),
        "covered": sum(r[4] for r in routes),
        "optimal": optimal,
    }
    return {"project": project.pk, "fingerprint": fingerprint, "routes": routes}, preview


@transaction.atomic
def apply_proposal(project_id, proposal):
    project = Project.objects.select_for_update().get(pk=project_id)
    nodes, graph, budgets, fingerprint = inputs(project)
    if proposal["project"] != project_id or proposal["fingerprint"] != fingerprint:
        raise ValidationError(
            "The plan or SDE changed. Close this preview and run Balance workforce again."
        )
    for source, destination, amount, route_id, _ in proposal["routes"]:
        if destination not in graph.get(source, ()):
            raise ValidationError("A proposed route no longer joins adjacent systems.")
        save_route(
            project_id, nodes[source], nodes[destination], amount, route_id, configure_modes=True
        )
    result = calculate_project(project)
    if any(not r["valid"] for r in result["routes"]) or any(
        b.workforce_left < min(0, budgets[b.system.pk].workforce_left)
        or (b.exported > b.initial_workforce and b.exported > budgets[b.system.pk].exported)
        for b in result["budgets"]
    ):
        raise ValidationError(
            "The proposal would worsen a workforce deficit. No changes were applied."
        )
