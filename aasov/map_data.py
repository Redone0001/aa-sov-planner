"""Schematic positions for display; geographic positions for all LY calculations."""

import math

from django.urls import reverse

from . import sde

LIGHT_YEAR = 9_460_000_000_000_000


def coordinates(system, fields):
    values = [getattr(system, field) for field in fields]
    return values if all(v is not None and math.isfinite(v) for v in values) else None


def distance(a, b):
    pa, pb = coordinates(a, ("x", "y", "z")), coordinates(b, ("x", "y", "z"))
    return math.dist(pa, pb) / LIGHT_YEAR if pa is not None and pb is not None else None


def zone(ly):
    if ly is None:
        return None
    # Exact distances, without gaps or rounding across a boundary.
    return next((i for i, limit in enumerate((5, 10, 15, 20), 1) if ly <= limit), 5)


def system_node(system, capital):
    ly = distance(system, capital) if capital else None
    return {
        "id": system.pk,
        "name": system.name,
        "position": coordinates(system, ("x_2d", "y_2d")),
        "geographic": coordinates(system, ("x", "y", "z")) is not None,
        "capital_distance": ly,
        "zone": zone(ly),
        "constellation": str(system.constellation.name) if system.constellation else "Unknown",
        "planned_id": None,
    }


def capital_system(project):
    return project.capital.solar_system if project.capital_id else None


def project_map(project, context, can_edit, can_manage):
    capital = capital_system(project)
    nodes = []
    for b in context["budgets"]:
        system = b.system
        node = system_node(system.solar_system, capital)

        def url(name, *args):
            return reverse(f"aasov:{name}", args=[project.pk, system.pk, *args])

        node.update(
            planned_id=system.pk,
            owner=system.owner_name if system.owner_observed_at else "Not captured yet",
            owner_id=system.owner_id,
            owner_observed_at=system.owner_observed_at.isoformat()
            if system.owner_observed_at
            else None,
            mode=system.get_mode_display(),
            warnings=b.warnings,
            power={
                "initial": b.initial_power,
                "left": b.power_left,
                "current_left": b.current.power_left,
            },
            workforce={
                "initial": b.initial_workforce,
                "left": b.workforce_left,
                "current_left": b.current.workforce_left,
                "imported": b.imported,
                "exported": b.exported,
                "transit": b.transiting,
            },
            upgrades=[
                {
                    "type_id": u.upgrade.item_type_id,
                    "name": u.upgrade.item_type.name,
                    "status": u.status,
                    "edit": url("upgrade_edit", u.pk) if can_edit else None,
                    "install": url("upgrade_installed", u.pk)
                    if can_edit and u.status == "planned"
                    else None,
                }
                for u in b.upgrades
            ],
            logistics=next(
                (
                    u.status
                    for u in b.upgrades
                    if (u.upgrade.item_type.name_en or u.upgrade.item_type.name).casefold()
                    == "advanced logistics network"
                ),
                None,
            ),
            actions={
                "Add upgrade": url("upgrade_add") if can_edit else None,
                "Workforce mode": url("mode") if can_edit else None,
                "Import workforce": reverse("aasov:route_add", args=[project.pk])
                + f"?destination={system.pk}"
                if can_edit and system.mode != "export"
                else None,
                "Export workforce": reverse("aasov:route_add", args=[project.pk])
                + f"?source={system.pk}"
                if can_edit and system.mode != "import" and not b.exports
                else None,
                "Remove system": url("system_remove") if can_manage else None,
            },
        )
        nodes.append(node)
    graph = sde.gate_graph([node["id"] for node in nodes])
    return {
        "nodes": nodes,
        "capital": capital.pk if capital else None,
        "capital_name": capital.name if capital else None,
        "gates": sorted(
            {tuple(sorted((a, b))) for a, neighbors in graph.items() for b in neighbors}
        ),
        "routes": [
            {
                "source": r["route"].source.solar_system_id,
                "destination": r["route"].destination.solar_system_id,
                "amount": r["route"].amount,
                "valid": r["valid"],
                "path": [system.solar_system_id for system in r["path"]],
                "edit": reverse("aasov:route_edit", args=[project.pk, r["route"].pk])
                if can_edit
                else None,
            }
            for r in context["routes"]
        ],
    }


def nearby_systems(project, source):
    position = coordinates(source, ("x", "y", "z"))
    if position is None:
        return {
            "nodes": [],
            "error": "This system has no geographic coordinates. Reload the SDE to calculate range.",
        }
    radius = 5 * LIGHT_YEAR
    filters = {
        f"{axis}__range": (value - radius, value + radius)
        for axis, value in zip(("x", "y", "z"), position)
    }
    candidates = (
        sde.eligible_systems()
        .filter(**filters)
        .exclude(pk=source.pk)
        .select_related("constellation")
    )
    capital = capital_system(project)
    nodes = []
    for system in candidates:
        ly = distance(source, system)
        if ly is not None and ly <= 5:
            node = system_node(system, capital)
            node["source_distance"] = ly
            nodes.append(node)
    return {"nodes": sorted(nodes, key=lambda n: (n["source_distance"], n["name"])), "error": None}
