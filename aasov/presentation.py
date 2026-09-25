"""Role-aware project presentation; simplified viewers never receive planning data."""

from collections import Counter
from types import SimpleNamespace

from django.db.models import Prefetch, Q

from .models import PlannedUpgrade
from .services import calculate_project


def simplified_view(project, user):
    return project.simplified_viewers and not (
        user.is_superuser
        or user.has_perm("aasov.edit_plan")
        or user.has_perm("aasov.manage_plan")
        or (user.is_staff and user.has_perm("aasov.change_project"))
    )


def project_context(project, user):
    if not simplified_view(project, user):
        return calculate_project(project)
    upgrades = (
        PlannedUpgrade.objects.filter(status__in=("online", "temporary"))
        .exclude(Q(upgrade__power_production__gt=0) | Q(upgrade__workforce_production__gt=0))
        .select_related("upgrade__item_type")
        .order_by("pk")
    )
    systems = (
        project.systems.select_related("solar_system__constellation")
        .prefetch_related(Prefetch("upgrades", queryset=upgrades, to_attr="visible_upgrades"))
        .order_by(
            "solar_system__constellation__name",
            "solar_system__constellation_id",
            "solar_system__name",
        )
    )
    budgets, groups, names, counts = [], {}, {}, Counter()
    for system in systems:
        row = SimpleNamespace(system=system, upgrades=system.visible_upgrades)
        budgets.append(row)
        constellation = system.solar_system.constellation
        groups.setdefault(constellation.pk, {"constellation": constellation, "budgets": []})[
            "budgets"
        ].append(row)
        for item in row.upgrades:
            names[item.upgrade_id] = item.upgrade.item_type.name
            counts[item.upgrade_id] += 1
    inventory = sorted(
        [{"name": names[pk], "all": count} for pk, count in counts.items()],
        key=lambda item: item["name"].casefold(),
    )
    return {
        "simplified_view": True,
        "budgets": budgets,
        "constellations": list(groups.values()),
        "routes": [],
        "inventory": inventory,
        "inventory_clipboard": {
            "visible": "\n".join(f"{item['name']}\t{item['all']}" for item in inventory)
        },
    }
