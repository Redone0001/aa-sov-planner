"""Bounded, preview-first CSV imports. Files never execute spreadsheet formulas."""

import csv
import io
import re
import unicodedata
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction
from eve_sde.models import SovereigntyUpgrade

from .models import PlannedUpgrade, Project
from .services import touch

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 256
SYSTEM_HEADERS = {
    "system",
    "systemname",
    "solarsystem",
    "solarsystemname",
    "systemid",
    "solarsystemid",
    "evesystemid",
}
UPGRADE_HEADERS = {
    "upgrade",
    "upgrades",
    "upgradename",
    "upgradenames",
    "upgradetypeid",
    "upgradetype",
    "upgradeid",
    "typeid",
    "sovupgrade",
    "sovereigntyupgrade",
}
ROMAN = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
}
YES = {
    "1",
    "1.0",
    "yes",
    "y",
    "true",
    "x",
    "✓",
    "checked",
    "planned",
    "online",
    "installed",
    "temporary",
    "offline",
}
NO = {"", "0", "0.0", "no", "n", "false", "-", "none", "n/a"}


def normalized(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).strip().casefold().split())


def header_key(value):
    return re.sub(r"[\W_]+", "", normalized(value))


def upgrade_key(value):
    value = normalized(value)
    value = re.sub(r"\b(major|minor) threat detection array\b", r"\1 threat", value)
    words = value.split()
    if words and words[-1] in ROMAN:
        words[-1] = ROMAN[words[-1]]
    return " ".join(words)


def catalog(project):
    systems = list(project.systems.select_related("solar_system"))
    upgrades = list(SovereigntyUpgrade.objects.select_related("item_type"))
    system_names, upgrade_names, families = defaultdict(set), defaultdict(set), defaultdict(set)
    for system in systems:
        system_names[normalized(system.solar_system.name)].add(system.pk)
    for upgrade in upgrades:
        for name in {upgrade.item_type.name, getattr(upgrade.item_type, "name_en", None)} - {
            None,
            "",
        }:
            key = upgrade_key(name)
            upgrade_names[key].add(upgrade.pk)
            tier = re.fullmatch(r"(.+) ([1-9]\d*)", key)
            if tier:
                families[tier[1]].add(upgrade.pk)
    return systems, upgrades, system_names, upgrade_names, families


def column_index(value, headers):
    value = value.strip()
    if value.isdecimal() and 1 <= int(value) <= len(headers):
        return int(value) - 1
    matches = [i for i, name in enumerate(headers) if header_key(name) == header_key(value)]
    if len(matches) == 1:
        return matches[0]
    raise ValidationError(
        f"Column '{value}' was not found uniquely. Use its 1-based column number."
    )


def layout(headers, system_column, upgrade_columns, upgrade_names, families):
    if system_column:
        system = column_index(system_column, headers)
    else:
        matches = [i for i, h in enumerate(headers) if header_key(h) in SYSTEM_HEADERS]
        if len(matches) > 1:
            id_matches = [
                i
                for i in matches
                if header_key(headers[i]) in {"systemid", "solarsystemid", "evesystemid"}
            ]
            if len(id_matches) == 1:
                matches = id_matches
        if len(matches) != 1:
            raise ValidationError(
                "Choose the system column by name or number; automatic detection requires one System/System name/System ID column."
            )
        system = matches[0]
    if upgrade_columns:
        columns = [
            (column_index(name, headers), "list")
            for name in upgrade_columns.split(",")
            if name.strip()
        ]
    else:
        columns = []
        for i, name in enumerate(headers):
            if i == system:
                continue
            key = header_key(name)
            if key in UPGRADE_HEADERS or re.fullmatch(
                r"(?:upgrade|upgradename|upgradetype|typeid|upgradeslot)\d+", key
            ):
                columns.append((i, "list"))
            elif upgrade_key(name) in upgrade_names:
                columns.append((i, "marker"))
            elif upgrade_key(name) in families:
                columns.append((i, "tier"))
    if not columns or any(i == system for i, _ in columns):
        raise ValidationError(
            "Choose at least one upgrade column different from the system column. Use Upgrade/Upgrade 1 headers, or upgrade names as column headers."
        )
    return system, list(dict.fromkeys(columns))


def read_csv(upload, system_column, upgrade_columns, delimiter, upgrade_names, families):
    raw = upload.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValidationError("CSV files must be no larger than 2 MiB.")
    if raw.startswith(b"PK\x03\x04"):
        raise ValidationError(
            "Export the workbook as CSV or TSV first; Excel workbook files are not supported."
        )
    try:
        text = raw.decode("utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("cp1252")
        except UnicodeDecodeError as error:
            raise ValidationError("Use UTF-8, UTF-16 or Windows-1252 CSV encoding.") from error
    if "\x00" in text:
        raise ValidationError("This does not look like a text CSV file. Export it as CSV first.")
    text = text.lstrip("\ufeff\r\n")
    if text[:4].lower() == "sep=" and len(text.splitlines()[0]) == 5:
        declared, _, text = text.partition("\n")
        delimiter = delimiter or declared[4]
    delimiters = [delimiter] if delimiter else [",", ";", "\t", "|"]
    errors = []
    for separator in delimiters:
        if separator not in {",", ";", "\t", "|"}:
            raise ValidationError("Unsupported delimiter. Choose comma, semicolon, tab or pipe.")
        try:
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=separator, strict=True)
            rows = []
            for row in reader:
                if not any(cell.strip() for cell in row):
                    continue
                if len(row) > MAX_COLUMNS or len(rows) >= MAX_ROWS + 20:
                    raise ValidationError(
                        "CSV limit exceeded: at most 5,000 data rows and 256 columns."
                    )
                rows.append((reader.line_num, [cell.strip() for cell in row]))
            for index, (_, headers) in enumerate(rows[:20]):
                try:
                    system, columns = layout(
                        headers, system_column, upgrade_columns, upgrade_names, families
                    )
                except ValidationError as error:
                    errors.extend(error.messages)
                    continue
                data = rows[index + 1 :]
                if len(data) > MAX_ROWS:
                    raise ValidationError("CSV imports support at most 5,000 data rows.")
                return headers, system, columns, data
        except csv.Error:
            errors.append("Malformed CSV quoting or an oversized cell. Export the sheet again.")
    raise ValidationError(errors[-1] if errors else "CSV is empty or has no usable header row.")


def resolve(value, names, ids, label):
    match = names.get(value, set())
    if re.fullmatch(r"\d+(?:\.0)?", value):
        pk = int(value.split(".")[0])
        if pk in ids:
            return ids[pk]
    if len(match) == 1:
        return next(iter(match))
    if len(match) > 1:
        raise ValidationError(f"Ambiguous {label}: '{value}'. Use its EVE ID.")
    raise ValidationError(
        f"Unknown {label}: '{value}'. Systems must already belong to this project; upgrades must exist in the sovereignty SDE."
    )


def parse_import(project, upload, system_column="", upgrade_columns="", delimiter=""):
    systems, upgrades, system_names, upgrade_names, families = catalog(project)
    headers, system_col, columns, data = read_csv(
        upload, system_column, upgrade_columns, delimiter, upgrade_names, families
    )
    system_ids = {s.solar_system_id: s.pk for s in systems}
    upgrade_ids = {u.pk: u.pk for u in upgrades}
    system_by_pk = {s.pk: s for s in systems}
    upgrade_by_pk = {u.pk: u for u in upgrades}
    existing = {
        (p.system_id, p.upgrade_id): p
        for p in PlannedUpgrade.objects.filter(system__project=project)
    }
    pairs, errors, duplicates, empty = {}, [], 0, 0
    for line, row in data:
        if row == headers:
            continue
        if len(row) > len(headers) and any(row[len(headers) :]):
            errors.append(
                f"Row {line}: extra cells have no column header. Check quoting or the delimiter."
            )
            continue
        row += [""] * max(0, len(headers) - len(row))
        selected = []
        try:
            for i, kind in columns:
                value = row[i]
                if kind == "list":
                    if upgrade_key(value) in upgrade_names:
                        selected.append(value)
                    else:
                        selected.extend(
                            part.strip()
                            for part in re.split(r"[,;|\n]", value)
                            if part.strip() and normalized(part) not in NO
                        )
                elif kind == "marker":
                    marker = normalized(value)
                    if marker in NO:
                        continue
                    if marker not in YES:
                        raise ValidationError(
                            f"'{headers[i]}' needs a yes/no marker, not '{value}'."
                        )
                    selected.append(headers[i])
                else:
                    tier = normalized(value)
                    if tier in NO:
                        continue
                    selected.append(f"{headers[i]} {ROMAN.get(tier, tier)}")
            if not selected:
                empty += 1
                continue
            system_id = resolve(normalized(row[system_col]), system_names, system_ids, "system")
            # Resolve the whole row before adding any of its pairs.
            upgrade_pks = [
                resolve(upgrade_key(value), upgrade_names, upgrade_ids, "upgrade")
                for value in selected
            ]
            for upgrade_id in upgrade_pks:
                pair = (system_id, upgrade_id)
                if pair in pairs:
                    duplicates += 1
                    continue
                old = existing.get(pair)
                pairs[pair] = {
                    "system": system_id,
                    "upgrade": upgrade_id,
                    "expected": [old.pk, old.status] if old else None,
                    "line": line,
                }
                if len(pairs) > MAX_ROWS:
                    raise ValidationError(
                        "Imports support at most 5,000 unique system/upgrade pairs."
                    )
        except ValidationError as error:
            errors.extend(f"Row {line}: {message}" for message in error.messages)
    if not pairs and not errors:
        errors.append("No upgrades found. Fill at least one system/upgrade row.")
    entries = list(pairs.values())
    display = []
    for entry in entries[:200]:
        old = entry["expected"]
        action = (
            "Add as Planned"
            if not old
            else ("Already Planned" if old[1] == "planned" else f"{old[1].title()} → Planned")
        )
        display.append(
            {
                "line": entry["line"],
                "system": str(system_by_pk[entry["system"]]),
                "upgrade": upgrade_by_pk[entry["upgrade"]].item_type.name,
                "action": action,
            }
        )
    used = {system_col} | {i for i, _ in columns}
    return {
        "entries": entries,
        "rows": display,
        "count": len(entries),
        "errors": errors[:50],
        "error_count": len(errors),
        "duplicates": duplicates,
        "empty": empty,
        "ignored": [name for i, name in enumerate(headers) if i not in used],
        "system_column": headers[system_col],
        "upgrade_columns": [headers[i] for i, _ in columns],
        "resets": sum(bool(e["expected"] and e["expected"][1] != "planned") for e in entries),
    }


@transaction.atomic
def apply_import(project_id, payload):
    project = Project.objects.select_for_update().get(pk=project_id)
    if payload["project"] != project.pk or not 0 < len(payload["entries"]) <= MAX_ROWS:
        raise ValidationError("Invalid import preview. Upload the CSV again.")
    entries = payload["entries"]
    system_ids = {e["system"] for e in entries}
    upgrade_ids = {e["upgrade"] for e in entries}
    if project.systems.filter(pk__in=system_ids).count() != len(
        system_ids
    ) or SovereigntyUpgrade.objects.filter(pk__in=upgrade_ids).count() != len(upgrade_ids):
        raise ValidationError(
            "Systems or upgrades changed since this preview. Upload the CSV again."
        )
    existing = {
        (p.system_id, p.upgrade_id): p
        for p in PlannedUpgrade.objects.filter(
            system__project=project, system_id__in=system_ids, upgrade_id__in=upgrade_ids
        )
    }
    creates, changes = [], []
    for entry in entries:
        old = existing.get((entry["system"], entry["upgrade"]))
        if ([old.pk, old.status] if old else None) != entry["expected"]:
            raise ValidationError(
                "An imported upgrade changed since this preview. Upload the CSV again to avoid overwriting another edit."
            )
        if old:
            if old.status != "planned":
                old.status = "planned"
                changes.append(old)
        else:
            creates.append(
                PlannedUpgrade(
                    system_id=entry["system"], upgrade_id=entry["upgrade"], status="planned"
                )
            )
    PlannedUpgrade.objects.bulk_create(creates)
    PlannedUpgrade.objects.bulk_update(changes, ["status"])
    touch(project)
    return len(creates), len(changes)
