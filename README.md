# Alliance Auth Sovereignty Planner

Plan sovereignty upgrades, power and workforce across EVE Online systems inside **Alliance Auth 5.x**. Create projects from regions or individual systems, compare the current setup with the intended plan, and manage workforce routes on a list or interactive map.

**Current release: [1.1.2](https://github.com/Redone0001/aa-sov-planner/releases/tag/v1.1.2)** · [Release notes](docs/releases/1.1.2.md) · [Theme verification](docs/theme-validation.md) · [MIT license](LICENSE)

## Features

- **Projects:** select regions and/or individual player-claimable nullsec systems in Django admin. Systems are deduplicated and start without upgrades. Plan Managers can remove systems; exclusions prevent later region saves from adding them back.
- **Two resource budgets:** current = Online + Temporary; planned = Online + Planned. Each setup has separate power/workforce balances, progress bars, fuel requirements and exclusivity checks. Offline upgrades count in neither.
- **Upgrade editing:** add, change, delete, mark installed or quickly set Offline. Remember the last status when adding multiple upgrades. Show deficits and conflicting upgrade tiers as warnings without blocking exploratory plans.
- **Workforce routing:** explicit source, destination and amount, with stargate paths through Transit systems. One destination per exporter; up to three sources per importer. Power and upgrade-generated workforce stay local.
- **Workforce balancing:** preview transfers from adjacent stargate neighbours to cover deficits while protecting both resource setups, existing routes and occupied transit systems.
- **Best ratting:** optimise a constellation's Major/Minor Threat upgrades and workforce routes. Higher tiers take priority; Major wins at equal tiers. Preserve Temporary and non-ratting upgrades and validate both resource setups before applying.
- **Exploration overlay:** green halos within five stargate jumps of Planned, Online or Temporary Exploration Detectors. Uses the complete SDE gate network, including paths outside the plan, and respects restricted viewer visibility. Zone colours remain visible inside systems, and red borders still mark resource warnings. Logistics links never extend this coverage.
- **Saved logistics links:** editors can connect two systems with Planned or Online Advanced Logistics Network, within 5 LY. Purple links appear on the map; links become invalid if their infrastructure is offlined or removed. Remove links from either endpoint’s sidebar.
- **Interactive map:** EVE SDE schematic layout with system names, ownership, upgrade icons, resource warnings and workforce arrows. Click systems to edit upgrades or remove routes. Pan, zoom and selection persist through edits.
- **Ansiblex planning:** highlight systems within 5 LY and colour distance zones from a saved project capital. Filter for Advanced Logistics present or online (including Temporary). Map spacing defaults to Compact; Ansiblex range defaults to None.
- **Ownership snapshots:** capture system ownership from public ESI when systems are added. Inspect capture status, timestamps and errors in admin, and retry missing snapshots.
- **CSV and inventory:** export upgrades and routes, import planned upgrades from varied spreadsheet layouts, and preview workforce-route imports. Download templates and copy upgrade totals as `item<TAB>quantity`.
- **Access controls:** Viewer, Editor and Plan Manager permissions; per-project restrictions to selected roles; an optional simplified Viewer view that hides planning data.
- **AA integration:** sidebar entry, AA's selected theme and inline edits without page reloads. Browser checks cover Flatly, Darkly and Materia.

## Requirements

- An existing **Alliance Auth 5.x** installation on **Django 5.2.x**, with its normal database, Redis and Celery services.
- A Python version supported by your AA installation. This package declares Python **3.10–3.14** support; integration tests run on Python **3.12**, AA **5.3.1** and Django **5.2.17**.
- **django-eveonline-sde >=0.2.0,<3** and loaded SDE data. Versions **0.2.0**, **1.0.0** and **2.0.0** are covered by integration tests. It is installed as a dependency, along with OR-Tools for optimisation.

AA 4.x is not supported. The package constrains `django-sri` to 0.8.x for AA 5.3.1 template compatibility. There is no PyPI release; install from GitHub or the release artifacts.

## Installation

Run the commands as your AA service user, with the **existing AA virtual environment activated**, from the directory containing your production `manage.py`.

### 1. Install version 1.1.2

The public repository uses HTTPS; no SSH key is needed:

```bash
python -m pip install --upgrade "git+https://github.com/Redone0001/aa-sov-planner.git@v1.1.2"
```

### 2. Enable the applications

In AA's `local.py`, put `modeltranslation` first:

```python
INSTALLED_APPS = ["modeltranslation"] + [
    app for app in INSTALLED_APPS if app != "modeltranslation"
]
```

Add these entries **once**. Omit `eve_sde` if it is already configured:

```python
INSTALLED_APPS += [
    "eve_sde",
    "aasov.apps.SovPlannerConfig",
]
```

### 3. Initialise the database and assets

```bash
python manage.py migrate
python manage.py esde_load_sde
python manage.py collectstatic --noinput
python manage.py check
```

Let the SDE load finish. Skip loading only if the installed SDE version has already completed its migrations and has complete, current data. When moving to SDE 2.0, follow the reload instructions below even if the previous SDE was current.

Restart AA's web service and Celery workers using your deployment's normal procedure. Workers must load the new app to process ownership snapshots. The module registers `/sov-planner/` automatically through AA's URL hook.

### 4. Assign permissions

Create AA groups with the following permissions. Permissions are additive; a higher role does not automatically grant the lower role's permissions.

| Role | Permissions | Capabilities |
| --- | --- | --- |
| Viewer | `aasov.view_project` | Open accessible plans, lists, maps, inventory and permitted CSV exports. |
| Editor | Viewer + `aasov.edit_plan` | Edit upgrades, modes and routes; use quick actions, balancing and Best ratting. |
| Plan Manager | Editor + `aasov.manage_plan` | Also remove systems, set capitals and import upgrade/route CSV files. |
| Administrator | Staff status + `aasov.add_project` / `aasov.change_project` as needed | Create/configure projects and retry missing ownership snapshots. Add `aasov.delete_project` to delete entire projects. Planner permissions remain separate. |

Users need to be logged in with a **main character set in AA** to open the planner. The sidebar entry requires `aasov.view_project`; a project's access restrictions are checked separately. Active superusers retain full access.

### 5. Create the first project

1. Open **Administration → Sovereignty Planner → Projects → Add**.
2. Enter a name and select regions and/or systems. Region selections include player-claimable nullsec systems; NPC systems, empire space and wormholes are excluded.
3. Configure project access if needed, then save. Ownership captures are queued to Celery.
4. Open **Sovereignty Planner** from the AA sidebar and select the project.
5. Add upgrades and workforce routes, or use the CSV import tools. New systems start in Transit mode.

## Project access and simplified Viewers

In the project's **Project access** section:

- Leave **Restrict project access** unchecked to allow all Viewers. Otherwise, choose any combination of Editors, Plan Managers and Administrators. Matching **any** selected role grants visibility; at least one role is required.
- Roles follow permissions, not group names. Administrators are staff users with `aasov.change_project`; staff status alone is insufficient. Visibility does not grant editing powers.
- Enable **Simplified view for Viewers** to show only Online and Temporary upgrades, excluding SDE power/workforce producers. Resource balances, warnings, fuel and workforce routes are omitted from lists, maps and exports. Inventory copy is filtered too.
- Editors, Plan Managers, Administrators and superusers keep the full planning view. Project visibility restrictions still apply.

Restrictions apply to project selectors, direct URLs, map data, imports/exports, edits and admin records. Both flags default to off.

## Upgrade states and workforce routes

| Upgrade status | Current budget | Planned budget |
| --- | --- | --- |
| Online | Counts | Counts |
| Temporary | Counts | Excluded |
| Planned | Excluded | Counts |
| Offline | Excluded | Excluded |

A Temporary tier can coexist with its Planned replacement. Multiple active tiers of the same upgrade family conflict within each setup.

Use **Import from…** or **Export to…** to select endpoints and an amount. Export-to choices list systems already in Import mode. Saving configures endpoint modes; intermediate systems must be Transit. The route preview shows the path before saving. The map sidebar also supports upgrade editing, Offline/Delete actions and route removal.

**Balance workforce** covers the larger workforce deficit per system using spare natural workforce available in both setups. It adds or increases direct-neighbour transfers while preserving existing routes. **Best ratting** can rearrange routes within a constellation, preserving boundary routes. Both tools preview changes before applying; signed previews expire after 15 minutes and are rejected when relevant inputs change.

## CSV and inventory

Open **CSV** in the plan header in either List or Map view.

| Operation | Format and behaviour |
| --- | --- |
| Export upgrades | `system,upgrade,status`; respects the user's permitted view. |
| Import upgrades | Plan Managers only. Download the template from **Import CSV**. Accepts system/upgrade names or IDs, multiple upgrade columns and upgrade-as-column layouts. Every imported entry becomes **Planned**, including existing Online/Temporary/Offline entries. |
| Export routes | `source,destination,workforce,path`; unavailable to simplified Viewers. |
| Import routes | Plan Managers only. Required columns: `source,destination,workforce`. Download the template from the import dialog. Add/update by source; preserve unlisted routes. Paths are recomputed; the exported path column is informational. |

Imports support common delimiters and extra columns, with limits of **2 MiB / 5,000 data rows**. Names are matched without case sensitivity; unknown or ambiguous entries are rejected. Route amounts must be positive integers without thousands separators. Route previews validate the final merged plan, including import/export limits and Transit paths, before applying atomically.

The expandable inventory line copies planned or all upgrades as `item<TAB>quantity`. Simplified Viewers can copy only their visible upgrades.

## Upgrading an existing installation

Install the release in the existing AA environment:

```bash
python -m pip install --upgrade "git+https://github.com/Redone0001/aa-sov-planner.git@v1.1.2"
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check
```

Restart AA web services and Celery workers, then refresh the browser. Existing plans are preserved. Version 1.1.2 adds no planner migrations compared with 1.1.0/1.1.1. Upgrades from 1.0.0 also apply `0008_logisticslink` for saved logistics links.

The dependency range accepts both SDE 0.2.0 and 2.0.0. To retain 0.2.0 or explicitly select 2.0.0, append `"django-eveonline-sde==0.2.0"` or `"django-eveonline-sde==2.0.0"` to the install command. Other AA plugins must support the chosen version too; upgrading this planner does not resolve their dependency constraints.

### Moving to SDE 2.0

Back up the database, configuration and installed package versions, and rehearse on a staging copy using the production database engine. Confirm that all installed plugins accept SDE 2.0.0 before installing it. Pause scheduled SDE updates during the migration and reload.

With SDE 2.0.0 installed, review and apply any pending migrations, then reload:

```bash
python manage.py migrate --plan
python manage.py migrate
python manage.py esde_load_sde --plan
python manage.py esde_load_sde --full
python manage.py check
python -m pip check
```

From SDE 0.2.0, migrations `0037`–`0040` add certificate/mastery tables, relation constraints and import tracking. The default source changes to CCP's REST API; the first REST load reloads every model. `ESDE_SOURCE = "zip"` remains available as an SDE 2.0 fallback. Review section counts and import logs, including failures to delete obsolete rows protected by saved plans, and verify existing plans before restarting normal services.

If reverting the SDE migration or reload, restore the matching database backup and previous package/configuration set together. Migration `0038` removes duplicate relation rows, so reversing migrations alone cannot recover them. If SDE 2.0 is already fully migrated and loaded, no additional reload is required solely for planner 1.1.2.

## SDE updates and ownership maintenance

For scheduled SDE updates, add this entry to your existing AA Celery Beat schedule. The hourly check follows SDE 2.0's recommendation and can also be used with SDE 0.2.0:

```python
from celery.schedules import crontab

CELERYBEAT_SCHEDULE["EVE SDE :: Check for SDE Updates"] = {
    "task": "eve_sde.tasks.check_for_sde_updates",
    "schedule": crontab(minute="30"),
}
```

Restart Celery Beat after changing its configuration. SDE updates affect resource values, upgrade costs and map paths; plans do not freeze historical SDE data.

Ownership snapshots are captured when systems are added, not continuously refreshed. Use **Capture missing ownership snapshots** in admin, or:

```bash
python manage.py aasov_snapshot_owners
```

Check worker logs and the admin snapshot diagnostics if captures remain queued or fail. To diagnose one project directly without Celery, use `python manage.py aasov_snapshot_owners --now --project 1` with its actual project ID. Existing snapshots are preserved.

## Planning assumptions

Natural power/workforce comes from SDE star and planet resources and assumes all skyhooks are active. The app does not query actual upgrade state, skyhook activity or fuel stock. Ownership is a snapshot; Ansiblex highlighting checks range, not whether a usable connection already exists.

Power never transfers. Generated workforce stays local. Transit workforce does not add to a transit system's budget. Resource over-allocation is allowed and warned about; invalid routes are excluded from budgets. Check the whole plan, since an importer can look adequately supplied while its exporter has a deficit.

This is an advisory planner. It does not make in-game changes or fully simulate all sovereignty installation rules.

## Development and verification

```bash
python -m pip install -e '.[test]'
pytest
ruff check aasov tests
python -m django check --settings=tests.aa_settings
python -m django makemigrations aasov --check --dry-run --settings=tests.aa_settings
python -m build
```

Integration tests use real AA apps/templates and SDE models with SQLite and a test Redis backend. Production database concurrency should also be verified on your deployment's MySQL/MariaDB database. Never use the test settings in production.

Run the test suite in separate environments for `django-eveonline-sde==0.2.0`, `==1.0.0` and `==2.0.0`; their modeltranslation requirements differ. `tests/test_sde_compatibility.py` exercises imported resources, upgrade costs/fuel, gate routes and saved references across reloads, including REST-formatted records on SDE 2.0.

SDE 0.2.0 remains accepted for existing installations and initial imports. Its upstream updater can fail with `bulk_update() cannot be used with primary key fields` when existing resource rows change; the regression suite records this as an expected failure. SDE 1.0.0 and 2.0.0 pass the changed-resource reload test.

See [theme and browser checks](docs/theme-validation.md). The screenshots and test fixtures use synthetic data.

## Credits and license

Built for [Alliance Auth](https://gitlab.com/allianceauth/allianceauth), using [django-eveonline-sde](https://pypi.org/project/django-eveonline-sde/) and the [Alliance Auth example app](https://github.com/ErikKalkoken/allianceauth_example) as a starting reference.

Released under the [MIT license](LICENSE). EVE Online names and data belong to their respective owners. Dependencies retain their own licenses.
