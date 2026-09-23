# Alliance Auth Sovereignty Planner

An installable Alliance Auth app for planning sovereignty upgrades and workforce routes. Admins create projects from systems and/or regions; readers inspect budgets; editors change upgrade states and workforce routing.

## Features

- Projects managed in Django admin. Region and system selections form a deduplicated union of player-claimable nullsec systems; new systems start with no upgrades.
- Read, Editor and Plan Manager permissions, assignable through normal AA groups/states. Plan Managers can remove systems without Django admin access.
- Compact, collapsible constellation groups, remembered upgrade status and per-constellation ratting optimisation with workforce routing.
- Creation-time ownership snapshots from public ESI, captured by the existing AA Celery workers.
- AA menu and Bootstrap 5 base template, using the selected AA theme, shared framework assets, and theme colors.
- Power and workforce initial, generated, used, remaining, imported/exported values; accessible progress bars, negative balances and explicit warnings.
- Planned and online upgrades consume resources, generate resources and require fuel. Offline upgrades do neither.
- Upgrade exclusivity warnings include SDE exclusion groups and different numbered tiers of the same upgrade family (for example Major Threat Detection Array II and III). Offline tiers do not conflict.
- SDE-backed resource conversion, hourly fuel requirements and startup fuel totals for planned upgrades.
- Import, transit and export modes. One destination per exporter, at most three exporters per importer. Shortest stargate paths through transit systems in the same project; intermediate systems cannot use passing workforce.
- Project-wide locking during edits to serialize route limits. POST-only deletion and Django CSRF protection. Read access never authorizes mutations.

## Preview

Screenshots use synthetic demonstration systems and upgrade values, rendered inside real AA themes:

- [Light theme](docs/planner-light.png)
- [Dark theme](docs/planner-dark.png)
- [Mobile view](docs/planner-mobile.png)
- [Flatly, Darkly and Materia verification](docs/theme-validation.md)

## Compatibility and installation

Targets **Alliance Auth 5.x**, Python 3.10–3.14, and **django-eveonline-sde 0.2.x**. The package requires `allianceauth>=5,<6`; AA 4.x is not supported. Integration tests use AA 5.3.1, Django 5.2.17, Python 3.12, and SDE 0.2.0. `django-sri` is constrained to 0.8.x because AA 5.3.1 uses its `sri_static` template tag, which is absent from 1.0.

Install from GitHub inside the existing AA virtual environment (this package is not yet published to PyPI):

```bash
python -m pip install --upgrade "git+https://github.com/Redone0001/aa-sov-planner.git@master"
```

In your AA `local.py`, ensure `modeltranslation` is first and add the SDE and planner once:

```python
INSTALLED_APPS = ["modeltranslation"] + [
    app for app in INSTALLED_APPS if app != "modeltranslation"
]
INSTALLED_APPS += [
    "eve_sde",                    # omit if already installed
    "aasov.apps.SovPlannerConfig",
]
```

Run with the normal AA settings and services:

```bash
python manage.py migrate
python manage.py esde_load_sde
python manage.py collectstatic --noinput
python manage.py check
```

Let the SDE import finish, then restart your AA web service/workers using your deployment's normal procedure. The package adds `/sov-planner/` through AA's URL hook. SDE import and ownership snapshots use AA’s existing Celery/Redis setup. Restart the workers when upgrading so they discover `aasov.tasks.snapshot_ownership`. OR-Tools is installed as a package dependency for ratting optimisation.

For automatic SDE updates, merge the upstream package's periodic task into your existing schedule:

```python
from celery.schedules import crontab

CELERYBEAT_SCHEDULE["EVE SDE :: Check for SDE Updates"] = {
    "task": "eve_sde.tasks.check_for_sde_updates",
    "schedule": crontab(minute="0", hour="12"),
}
```

Grant `aasov.view_project` to readers. Editors need **both** `aasov.view_project` and `aasov.edit_plan`. For **Plan Managers**, create an AA group with `aasov.view_project`, `aasov.edit_plan` and `aasov.manage_plan` (displayed as “Plan Manager: can remove systems from plans”). The management permission adds system removal; staff/superuser status is not needed. Staff creating projects also need normal Project add/change permissions; these do not automatically grant planner edit access. AA's usual login/main-character requirements apply. Permissions are global to the module: authorized readers can see every project.

## Use

1. Open **Administration → Sovereignty Planner → Projects → Add**. Enter a name and select regions and/or individual systems. Regions include player-claimable nullsec systems only; NPC-owned systems, empire space and wormholes are excluded.
2. Open **Sovereignty Planner** from the AA menu and choose the project.
3. Add upgrades and choose Planned, Online or Offline. Save even if the budget is negative; a red warning identifies the system and deficit. Exclusivity conflicts are saved as warnings too, so alternative plans can be compared.
4. Click **Import from…** or **Export to…** beside a system, then choose the other endpoint and workforce amount. **Export to…** lists only systems already in Import mode; set the receiving system to Import first if needed. Saving sets the source to Export and the receiver to Import. Intermediate systems must be **Transit**; all new systems default to Transit.
5. Check the live route preview before saving: it shows the complete shortest stargate path through the project’s Transit systems. Connectivity is checked again when saved. Disconnected paths, self-imports, exceeded import/export limits and changes that would break existing routes are rejected. Each system displays its sources, destination and route paths.
6. Inspect the route paths, local balances and fuel requirements. Adjust or remove routes before changing a mode that would break connectivity. Alternate valid transit paths are found automatically.

Upgrade, mode and route edits open in an AA-themed dialog and refresh budgets without reloading the page. The system filter and table scroll are preserved. When adding upgrades, leave **Keep open to add another upgrade** checked to add several in succession. The last successfully saved upgrade status is reused for new upgrades across systems in your login session; editing an existing upgrade retains its own status. Standard form pages remain available without JavaScript.

Plan Managers can use **Remove system** and confirm the affected system in a dialog. Its upgrades and endpoint routes are deleted. Transit routes are recalculated and flagged if connectivity breaks. The system stays excluded from later region saves. An administrator can clear it from **Excluded systems** and save to add it back empty.

Saving a project's selections **adds** missing systems. It never removes planned systems or resets upgrades if a region/system is deselected. To start again, create a new project; deleting an entire project through the normal admin confirmation removes its plan. Projects are independent even when they contain the same EVE systems.

## Upgrade to 0.2.0

In the AA virtual environment and project directory:

```bash
python -m pip install --upgrade "git+https://github.com/Redone0001/aa-sov-planner.git@master"
python manage.py migrate
python manage.py collectstatic --noinput
```

Restart AA web services and Celery workers, then run `python manage.py aasov_snapshot_owners` to populate missing ownership snapshots on existing plans. Grant the new `aasov.manage_plan` permission to the intended Plan Manager group. Refresh the browser to load the updated JavaScript and layout.

## Ownership snapshots

When new systems are added, a task queries public ESI sovereignty ownership and resolves owner names in batches. The page displays the claiming alliance (or faction/unclaimed), with the observation time in Details and the owner tooltip. Snapshots are not continuously refreshed. Failed requests retry; until a snapshot succeeds the page says **Not captured yet**, never “Unclaimed” based on missing data. No character token or new ESI scopes are required.

After upgrading, fill missing snapshots on existing projects (these reflect ownership at the time of the backfill, not historic creation):

```bash
python manage.py aasov_snapshot_owners
```

This command also retries missing snapshots if the worker/broker was unavailable at creation; existing snapshots remain unchanged.

## Best ratting

Expand a constellation and click **Best ratting**. Editors receive a preview with current/proposed active ratting upgrades, system modes, remaining budgets, and old/new workforce routes. Apply the preview to update the plan without a page reload.

- The objective first maximises the number of level III upgrades, then level II, then level I. One higher-level upgrade outweighs any number of lower-level upgrades. Only after those counts tie does it prefer Major over Minor (III, then II, then I).
- Both Major and Minor families can be selected where resources and SDE exclusion groups permit; multiple active tiers within one family are forbidden.
- Power/workforce come from the SDE. Non-ratting upgrades remain unchanged. Replaced ratting upgrades become Offline, newly activated upgrades become Planned, and retained Online ratting upgrades stay Online.
- The optimiser may replace internal workforce routes and change modes **within the selected constellation**. New paths use only its planned systems. Routes that cross its boundary (including paths through other constellations) and the modes needed for their current paths are preserved.
- Every system must have nonnegative resource balances. Generated workforce stays local; sources export to at most one destination, destinations import from at most three sources, and intermediate systems must be Transit. Fuel supply and live ownership eligibility are not optimisation constraints.
- OR-Tools has a 10-second solver budget and a limit of 40 planned systems per constellation. A feasible solution not proven optimal is explicitly labelled. Ties favour fewer routes, then less transferred workforce and fewer mode changes when time allows.
- Previews expire after 15 minutes and cannot be applied if project inputs or relevant SDE data changed. Missing SDE data or an infeasible preserved baseline returns an explanation without changing the plan.

## Budget semantics and scope

Data is read directly from `eve_sde` on each page load:

| Input | SDE model/fields |
| --- | --- |
| Natural resources | `StarResource` + `PlanetResource`: `power`, `workforce` |
| Upgrade consumption | `SovereigntyUpgrade.power_allocation`, `workforce_allocation` |
| Conversion output | `power_production`, `workforce_production` |
| Fuel | `fuel_item_type`, `hourly_upkeep`, `startup_cost` |
| Exclusivity | `mutually_exclusive_group` |
| Connectivity | `Stargate.solar_system`, `destination` |

```text
Power left = natural power + active production − active allocation
Workforce left = natural workforce + active production + imports − exports − active allocation
```

- Natural resources assume all system skyhooks are active. Missing resource rows produce a warning; values are never presented as a verified zero-capacity system silently.
- Projects represent systems belonging to **one alliance**. Ownership is an ESI snapshot, not live eligibility validation. Skyhook activity, upgrade online state and available fuel are not queried. No ESI credentials or in-game changes are involved.
- Workforce amounts are planning allocations, entered manually or proposed by Best ratting. Over-allocation is allowed to expose deficits, and an export above natural workforce is explicitly flagged because generated workforce cannot leave its system. A recipient can therefore have a provisional positive budget while its exporter has a shortage; check all project warnings before treating a plan as feasible.
- Power never transfers. Transit flow is displayed separately and adds nothing to the transit system's local budget. The three-source limit applies to final imports, not transit hops.
- Fuel values are requirements, not stock checks or supply-chain simulation. Planned upgrades include startup cost; online upgrades already started; offline upgrades contribute zero.
- SDE updates immediately affect displayed budgets and paths. Broken routes are flagged and excluded from calculations. Automatic routes may change when connectivity/modes change. Plans do not snapshot historical SDE values.
- This is an advisory resource planner, not a complete simulation of all in-game installation, security, sovereignty ownership, priority or low-power-state restrictions. It intentionally allows infeasible resource plans, as requested.

## Development and validation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
pytest
ruff check aasov tests
python -m django check --settings=tests.aa_settings
python -m django makemigrations aasov --check --dry-run --settings=tests.aa_settings
python -m build
```

The standard AA dependency installation requires MySQL/MariaDB client development libraries. Local tests use SQLite and an in-memory Redis test backend, while loading real AA apps, URL wrappers, templates, menu and SDE migrations. Tests cover calculation conservation, conversion and fuel, route topology and limits, SDE changes, project population, permissions, CSRF and the editor/admin flows. Production database concurrency behavior still needs verification on the deployment's MariaDB/MySQL instance; SQLite does not implement row-level `SELECT FOR UPDATE`.

`tests.settings` is a lightweight alternative for calculation tests only (`pytest tests/test_planning.py --ds=tests.settings`). Never use the test settings or test secret in production.

## References

- [Alliance Auth example](https://github.com/ErikKalkoken/allianceauth_example): standalone app/menu/URL/permission structure. This app uses current Bootstrap 5 conventions instead of the example's legacy Django APIs.
- [Alliance Auth](https://gitlab.com/allianceauth/allianceauth)
- [django-eveonline-sde](https://pypi.org/project/django-eveonline-sde/)
- [Reference planner sheet](https://docs.google.com/spreadsheets/d/1hYivgA44y1REZLPMbeSMWctUhC06vAMU_WExcAtftiE/edit?gid=346648555): system budgets, upgrades and transit columns.
- [CCP sovereignty hub rules](https://support.eveonline.com/hc/en-us/articles/14339751569436-Sovereignty-Hub)
- [CCP workforce routing explanation](https://www.eveonline.com/news/view/sovereignty-updates-transition-and-upgrades)

## License

MIT. EVE Online names and data belong to their respective owners. Alliance Auth and django-eveonline-sde are separate dependencies under their own licenses.
