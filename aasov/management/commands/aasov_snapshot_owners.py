from django.core.management.base import BaseCommand

from aasov.models import Project
from aasov.tasks import snapshot_ownership


class Command(BaseCommand):
    help = "Queue missing sovereignty ownership snapshots (existing snapshots are preserved)."

    def handle(self, *args, **options):
        for project in Project.objects.all():
            ids = list(
                project.systems.filter(owner_observed_at__isnull=True).values_list(
                    "solar_system_id", flat=True
                )
            )
            if ids:
                snapshot_ownership.delay(project.pk, ids)
                self.stdout.write(f"Queued {len(ids)} systems for {project.name}")
