from django.core.exceptions import ValidationError
from django.db import models


class Project(models.Model):
    name = models.CharField(max_length=150, unique=True)
    description = models.TextField(blank=True)
    regions = models.ManyToManyField("eve_sde.Region", blank=True)
    selected_systems = models.ManyToManyField("eve_sde.SolarSystem", blank=True)
    excluded_systems = models.ManyToManyField(
        "eve_sde.SolarSystem", blank=True, related_name="excluded_sov_projects"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("name",)
        permissions = [
            ("edit_plan", "Can edit sovereignty plans"),
            ("manage_plan", "Plan Manager: can remove systems from plans"),
        ]

    def __str__(self):
        return self.name


class PlannedSystem(models.Model):
    class Mode(models.TextChoices):
        IMPORT = "import", "Import"
        TRANSIT = "transit", "Transit"
        EXPORT = "export", "Export"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="systems")
    solar_system = models.ForeignKey("eve_sde.SolarSystem", on_delete=models.PROTECT)
    mode = models.CharField(max_length=7, choices=Mode.choices, default=Mode.TRANSIT)

    owner_id = models.BigIntegerField(null=True, blank=True)
    owner_name = models.CharField(max_length=255, blank=True)
    owner_kind = models.CharField(max_length=12, default="unknown")
    owner_observed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("solar_system__name",)
        constraints = [
            models.UniqueConstraint(
                fields=("project", "solar_system"), name="aasov_project_system_unique"
            )
        ]
        default_permissions = ()

    def __str__(self):
        return str(self.solar_system.name)


class PlannedUpgrade(models.Model):
    class Status(models.TextChoices):
        PLANNED = "planned", "Planned"
        ONLINE = "online", "Online"
        OFFLINE = "offline", "Offline"

    system = models.ForeignKey(PlannedSystem, on_delete=models.CASCADE, related_name="upgrades")
    upgrade = models.ForeignKey("eve_sde.SovereigntyUpgrade", on_delete=models.PROTECT)
    status = models.CharField(max_length=7, choices=Status.choices, default=Status.PLANNED)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("system", "upgrade"), name="aasov_system_upgrade_unique"
            )
        ]
        default_permissions = ()


class WorkforceRoute(models.Model):
    source = models.OneToOneField(
        PlannedSystem, on_delete=models.CASCADE, related_name="export_route"
    )
    destination = models.ForeignKey(
        PlannedSystem, on_delete=models.CASCADE, related_name="import_routes"
    )
    amount = models.PositiveIntegerField(help_text="Workforce allocated from this source.")

    class Meta:
        default_permissions = ()
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="aasov_route_positive"),
            models.CheckConstraint(
                condition=~models.Q(source=models.F("destination")), name="aasov_route_distinct"
            ),
        ]

    def clean(self):
        super().clean()
        if not self.source_id or not self.destination_id:
            return
        if self.source.project_id != self.destination.project_id:
            raise ValidationError("Both systems must belong to the same project.")
        if self.source_id == self.destination_id:
            raise ValidationError("A system cannot export to itself.")
        if self.source.mode != PlannedSystem.Mode.EXPORT:
            raise ValidationError("The source system must be in Export mode.")
        if self.destination.mode != PlannedSystem.Mode.IMPORT:
            raise ValidationError("The destination system must be in Import mode.")
        if (
            WorkforceRoute.objects.filter(destination=self.destination).exclude(pk=self.pk).count()
            >= 3
        ):
            raise ValidationError("A system can import from at most three source systems.")
        from .services import find_route, project_graph

        nodes, graph = project_graph(self.source.project_id)
        if find_route(self.source_id, self.destination_id, nodes, graph) is None:
            raise ValidationError("No stargate path exists through the project's Transit systems.")
