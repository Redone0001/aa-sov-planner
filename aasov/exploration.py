"""Exploration coverage uses jump topology, independently of workforce routes."""

import re
from collections import defaultdict, deque

from eve_sde.models import Stargate


def upgrade_name(upgrade):
    return (upgrade.item_type.name_en or upgrade.item_type.name).strip().casefold()


def is_logistics(upgrade):
    return upgrade_name(upgrade) == "advanced logistics network"


def is_exploration(upgrade):
    return bool(
        re.fullmatch(r"(?:.* )?exploration detector(?: (?:[123]|i{1,3}))?", upgrade_name(upgrade))
    )


def coverage(sources):
    """Multi-source BFS over the SDE, including paths outside the selected plan."""
    if not sources:
        return {}
    graph = defaultdict(set)
    for a, b in Stargate.objects.values_list("solar_system_id", "destination_id").iterator():
        if a is not None and b is not None:
            graph[a].add(b)
            graph[b].add(a)
    result = {source: 0 for source in sources}
    queue = deque(sources)
    while queue:
        node = queue.popleft()
        if result[node] == 5:
            continue
        for neighbor in graph[node]:
            if neighbor not in result:
                result[neighbor] = result[node] + 1
                queue.append(neighbor)
    return result
