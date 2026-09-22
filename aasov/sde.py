"""Queries against django-eveonline-sde 0.2; no copied or hardcoded resource values."""

from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Count, Q, Sum
from eve_sde.models import Planet, PlanetResource, SolarSystem, Stargate, StarResource


@dataclass
class Resources:
    power: int = 0
    workforce: int = 0
    complete: bool = False


def eligible_systems():
    # Player-claimable nullsec in known space; NPC-owned and wormhole systems excluded.
    return SolarSystem.objects.filter(
        id__gte=30000000,
        id__lt=31000000,
        security_status__lte=0,
        faction_id_raw__isnull=True,
    )


def resources_for(system_ids):
    result = {pk: Resources() for pk in system_ids}
    stars = set()
    for row in StarResource.objects.filter(star__solar_system_id__in=system_ids).values(
        "star__solar_system_id", "power", "workforce"
    ):
        pk = row["star__solar_system_id"]
        result[pk].power += row["power"] or 0
        result[pk].workforce += row["workforce"] or 0
        stars.add(pk)
    planets = dict(
        Planet.objects.filter(solar_system_id__in=system_ids)
        .values("solar_system_id")
        .annotate(count=Count("id"))
        .values_list("solar_system_id", "count")
    )
    for row in (
        PlanetResource.objects.filter(planet__solar_system_id__in=system_ids)
        .values("planet__solar_system_id")
        .annotate(
            power_total=Sum("power"), workforce_total=Sum("workforce"), count=Count("planet_id")
        )
    ):
        pk = row["planet__solar_system_id"]
        result[pk].power += row["power_total"] or 0
        result[pk].workforce += row["workforce_total"] or 0
        result[pk].complete = pk in stars and row["count"] == planets.get(pk, 0)
    for pk in stars:
        if not planets.get(pk):
            result[pk].complete = True
    return result


def gate_graph(system_ids):
    graph = defaultdict(set)
    for source, target in Stargate.objects.filter(
        Q(solar_system_id__in=system_ids) & Q(destination_id__in=system_ids)
    ).values_list("solar_system_id", "destination_id"):
        graph[source].add(target)
        graph[target].add(source)
    return graph
