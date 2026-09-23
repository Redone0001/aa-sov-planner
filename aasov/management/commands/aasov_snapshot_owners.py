from django.core.management.base import BaseCommand, CommandError

from aasov.models import PlannedSystem, Project
from aasov.tasks import capture_ownership, queue_ownership_snapshot


class Command(BaseCommand):
    help = "Capture missing ownership snapshots. Defaults to Celery; --now runs directly for diagnosis. Existing snapshots are preserved."

    def add_arguments(self, parser):
        parser.add_argument(
            "--now", action="store_true", help="Run directly without the Celery worker/broker."
        )
        parser.add_argument("--project", type=int, help="Limit to one project ID.")

    def handle(self, *args, **options):
        projects = Project.objects.all()
        if options["project"] is not None:
            projects = projects.filter(pk=options["project"])
            if not projects.exists():
                raise CommandError("Project not found.")
        failures = []
        for project in projects:
            ids = list(
                project.systems.filter(owner_observed_at__isnull=True).values_list(
                    "solar_system_id", flat=True
                )
            )
            if not ids:
                self.stdout.write(f"{project.name}: no missing snapshots")
                continue
            try:
                if options["now"]:
                    count = capture_ownership(project.pk, ids)
                    self.stdout.write(f"{project.name}: captured {count} ownership snapshots")
                else:
                    count = queue_ownership_snapshot(project.pk, ids)
                    self.stdout.write(
                        f"{project.name}: queued {count} systems; check ownership status in admin"
                    )
            except Exception:
                error = (
                    PlannedSystem.objects.filter(project=project, owner_observed_at__isnull=True)
                    .exclude(owner_error="")
                    .values_list("owner_error", flat=True)
                    .first()
                )
                failures.append(
                    f"{project.name}: {error or 'Lookup failed; check the worker logs.'}"
                )
        if failures:
            raise CommandError("\n".join(failures))
