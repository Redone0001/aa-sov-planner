"""Preview and atomically merge workforce routes from CSV."""

import csv
import hashlib
import io
import json
from collections import Counter, defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction

from .csv_import import MAX_BYTES, MAX_COLUMNS, MAX_ROWS, header_key, normalized, resolve
from .models import PlannedSystem, Project, WorkforceRoute
from .services import calculate_project, find_route, project_graph, touch

HEADERS = {
    "source": {
        "source",
        "sourcesystem",
        "sourceid",
        "fromsystem",
        "sourcesystemid",
        "from",
        "exporter",
        "exportsystem",
    },
    "destination": {
        "destination",
        "destinationsystem",
        "destinationid",
        "tosystem",
        "destinationsystemid",
        "to",
        "importer",
        "importsystem",
    },
    "workforce": {
        "workforce",
        "amount",
        "quantity",
        "workforceamount",
        "transfer",
        "transferredworkforce",
    },
}


def read_rows(upload):
    raw = upload.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValidationError("CSV files must be no larger than 2 MiB.")
    if raw.startswith(b"PK\x03\x04"):
        raise ValidationError("Export the workbook as CSV or TSV first.")
    try:
        text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1252")
        except UnicodeDecodeError as error:
            raise ValidationError("Use UTF-8, UTF-16 or Windows-1252 CSV encoding.") from error
    if "\x00" in text:
        raise ValidationError("Export a text CSV file first.")
    text = text.lstrip("\ufeff\r\n")
    separators = [",", ";", "\t", "|"]
    if text[:4].lower() == "sep=" and len(text.splitlines()[0]) == 5:
        declared, _, text = text.partition("\n")
        if declared[4] not in separators:
            raise ValidationError("Unsupported CSV separator.")
        separators = [declared[4]]
    for separator in separators:
        try:
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=separator, strict=True)
            rows = []
            for row in reader:
                if not any(c.strip() for c in row):
                    continue
                if len(row) > MAX_COLUMNS or len(rows) >= MAX_ROWS + 20:
                    raise ValidationError("CSV limit exceeded: 5,000 rows and 256 columns.")
                rows.append((reader.line_num, [c.strip() for c in row]))
        except csv.Error:
            continue
        for index, (_, headers) in enumerate(rows[:20]):
            columns = {
                key: [i for i, name in enumerate(headers) if header_key(name) in aliases]
                for key, aliases in HEADERS.items()
            }
            if not all(len(matches) == 1 for matches in columns.values()):
                continue
            data = rows[index + 1 :]
            if len(data) > MAX_ROWS:
                raise ValidationError("CSV imports support at most 5,000 data rows.")
            columns = {key: matches[0] for key, matches in columns.items()}
            return data, columns, [h for i, h in enumerate(headers) if i not in columns.values()]
    raise ValidationError(
        "Provide one source, destination and workforce column. Supported separators: comma, semicolon, tab or pipe."
    )


def snapshot(project):
    nodes, graph = project_graph(project.pk)
    routes = list(WorkforceRoute.objects.filter(source__project=project).order_by("pk"))
    budgets = calculate_project(project)["budgets"]
    raw = {
        "systems": [[pk, n.solar_system_id, n.mode] for pk, n in sorted(nodes.items())],
        "gates": [[pk, sorted(edges)] for pk, edges in sorted(graph.items())],
        "routes": [[r.pk, r.source_id, r.destination_id, r.amount] for r in routes],
        "updated": Project.objects.values_list("updated_at", flat=True)
        .get(pk=project.pk)
        .isoformat(),
        "budgets": [
            [b.system.pk, b.workforce_left, b.current.workforce_left, b.initial_workforce]
            for b in budgets
        ],
    }
    fingerprint = hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest()
    return nodes, graph, routes, budgets, fingerprint


def validate_routes(nodes, graph, existing, entries):
    merged = {r.source_id: (r.destination_id, r.amount) for r in existing}
    for source, destination, amount in entries:
        if (
            source not in nodes
            or destination not in nodes
            or source == destination
            or not 1 <= amount <= 2147483647
        ):
            raise ValidationError("Invalid route endpoints or workforce amount.")
        merged[source] = (destination, amount)
    destinations = Counter(d for d, _ in merged.values())
    if any(count > 3 for count in destinations.values()):
        raise ValidationError("The resulting plan has more than three sources for an importer.")
    if set(merged) & destinations.keys():
        raise ValidationError(
            "A system cannot both import and export workforce. Fix the CSV or existing routes."
        )
    for source in merged:
        nodes[source].mode = "export"
    for destination in destinations:
        nodes[destination].mode = "import"
    paths = {}
    for source, (destination, _) in merged.items():
        path = find_route(source, destination, nodes, graph)
        if path is None:
            raise ValidationError(
                f"No valid path for {nodes[source]} → {nodes[destination]}. Intermediate systems must be in this plan and in Transit mode; other imported endpoints cannot be used for transit."
            )
        paths[source] = path
    return merged, paths


def parse_import(project, upload):
    rows, columns, ignored = read_rows(upload)
    nodes, graph, routes, budgets, fingerprint = snapshot(project)
    names = defaultdict(set)
    for pk, node in nodes.items():
        names[normalized(str(node))].add(pk)
    ids = {n.solar_system_id: pk for pk, n in nodes.items()}
    entries, seen, errors, duplicates = [], {}, [], 0
    for line, row in rows:
        try:
            if max(columns.values()) >= len(row):
                raise ValidationError("Missing source, destination or workforce value.")
            source = resolve(normalized(row[columns["source"]]), names, ids, "source system")
            destination = resolve(
                normalized(row[columns["destination"]]), names, ids, "destination system"
            )
            amount_text = row[columns["workforce"]]
            if (
                not amount_text.isascii()
                or not amount_text.isdigit()
                or len(amount_text) > 10
                or not 1 <= int(amount_text) <= 2147483647
            ):
                raise ValidationError(
                    "Workforce must be a positive whole number without thousands separators (maximum 2147483647)."
                )
            entry = [source, destination, int(amount_text)]
            if source in seen:
                if seen[source] != entry:
                    raise ValidationError(
                        "Conflicting rows for the same exporter. Each source can have only one destination and amount."
                    )
                duplicates += 1
                continue
            entries.append(entry)
            seen[source] = entry
        except ValidationError as error:
            errors.extend(f"Row {line}: {message}" for message in error.messages)
    if errors:
        raise ValidationError(errors[:50])
    if not entries:
        raise ValidationError("No workforce routes found in this file.")
    old_modes = {pk: n.mode for pk, n in nodes.items()}
    merged, paths = validate_routes(nodes, graph, routes, entries)
    old = {r.source_id: r for r in routes}
    preview_rows = [
        {
            "source": nodes[s],
            "destination": nodes[d],
            "amount": amount,
            "previous": f"{nodes[old[s].destination_id]} · {old[s].amount:,}"
            if s in old
            else "None",
            "path": [nodes[pk] for pk in paths[s]],
        }
        for s, d, amount in entries
    ]
    flow = Counter()
    for source, (destination, amount) in merged.items():
        flow[source] -= amount
        flow[destination] += amount
    balances = [
        {
            "system": b.system,
            "planned": b.workforce_left - b.imported + b.exported + flow[b.system.pk],
            "current": b.current.workforce_left - b.imported + b.exported + flow[b.system.pk],
            "overexport": merged.get(b.system.pk, (None, 0))[1] > b.initial_workforce,
        }
        for b in budgets
    ]
    preview = {
        "rows": preview_rows[:200],
        "count": len(entries),
        "duplicates": duplicates,
        "ignored": ignored,
        "modes": [
            {"system": n, "before": old_modes[pk], "after": n.mode}
            for pk, n in nodes.items()
            if n.mode != old_modes[pk]
        ],
        "deficits": [
            b for b in balances if b["planned"] < 0 or b["current"] < 0 or b["overexport"]
        ],
    }
    return {"project": project.pk, "fingerprint": fingerprint, "entries": entries}, preview


@transaction.atomic
def apply_import(project_id, payload):
    project = Project.objects.select_for_update().get(pk=project_id)
    nodes, graph, routes, _, fingerprint = snapshot(project)
    if payload["project"] != project_id or payload["fingerprint"] != fingerprint:
        raise ValidationError("The plan or SDE changed since this preview. Upload the CSV again.")
    validate_routes(nodes, graph, routes, payload["entries"])
    PlannedSystem.objects.bulk_update(list(nodes.values()), ["mode"])
    existing = {r.source_id: r for r in routes}
    changes, creates = [], []
    for source, destination, amount in payload["entries"]:
        if source in existing:
            route = existing[source]
            route.destination_id, route.amount = destination, amount
            changes.append(route)
        else:
            creates.append(
                WorkforceRoute(source_id=source, destination_id=destination, amount=amount)
            )
    WorkforceRoute.objects.bulk_update(changes, ["destination", "amount"])
    WorkforceRoute.objects.bulk_create(creates)
    touch(project)
    return len(creates), len(changes)
