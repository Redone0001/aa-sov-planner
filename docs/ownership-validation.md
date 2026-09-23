# Ownership admin verification (0.2.2)

Verified on AA 5.3.1 / Django 5.2.17:

- Project admin shows snapshot counts and read-only system/owner/time/status/error rows.
- Planned-system admin provides pagination, filtering, system details and a permission-checked missing-snapshot action.
- Tests cover broker failures, ESI HTTP errors, partial responses, preserving captured snapshots, admin permissions and the synchronous diagnostic command.
- A live public ESI lookup through `aasov_snapshot_owners --now --project …` captured an alliance name and timestamp in an isolated local test project. This does not establish the cause of missing snapshots on a deployment that has not been inspected.
- Browser checks found no JavaScript errors on the project and system admin pages.

Screenshots use synthetic demonstration data: [project admin](ownership-project-admin.png), [systems admin](ownership-systems-admin.png).
