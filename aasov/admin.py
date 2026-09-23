from django import forms
from django.contrib import admin, messages
from django.db.models import Count, Q
from django.urls import reverse
from django.utils.html import format_html, format_html_join

from .models import PlannedSystem, Project
from .sde import eligible_systems
from .services import sync_project
from .tasks import queue_ownership_snapshot


class ProjectAdminForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["selected_systems"].queryset = eligible_systems()

    def clean(self):
        data = super().clean()
        if not data.get("regions") and not data.get("selected_systems") and not self.instance.pk:
            raise forms.ValidationError("Select at least one region or solar system.")
        return data


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    form = ProjectAdminForm
    list_display = ("name", "updated_at", "ownership_summary")
    actions = ("capture_missing_ownership",)
    search_fields = ("name",)
    filter_horizontal = ("regions", "selected_systems", "excluded_systems")
    readonly_fields = ("updated_at", "ownership_snapshots")
    fieldsets = (
        ("Ownership snapshots", {"fields": ("ownership_snapshots",)}),
        (None, {"fields": ("name", "description", "updated_at")}),
        (
            "Add systems to this project",
            {
                "fields": ("regions", "selected_systems", "excluded_systems"),
                "description": "Regions add all player-claimable nullsec systems. Selections are combined and deduplicated. Saving adds missing systems without upgrades. Removing selections preserves existing plans. Excluded systems stay removed; clear an exclusion and save to add the system again.",
            },
        ),
    )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        sync_project(form.instance)

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .annotate(
                _system_count=Count("systems"),
                _snapshot_count=Count(
                    "systems", filter=Q(systems__owner_observed_at__isnull=False)
                ),
            )
        )

    @admin.display(description="Ownership snapshots")
    def ownership_summary(self, obj):
        return f"{obj._snapshot_count} / {obj._system_count} captured"

    @admin.display(description="Systems and ownership")
    def ownership_snapshots(self, obj):
        if not obj or not obj.pk:
            return "Systems and ownership snapshots appear after the project is created."
        systems = obj.systems.select_related("solar_system").all()[:50]
        rows = format_html_join(
            "",
            '<tr><td><a href="{}">{}</a></td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>',
            (
                (
                    reverse("admin:aasov_plannedsystem_change", args=[system.pk]),
                    str(system),
                    system.owner_name or "Unknown",
                    system.owner_observed_at or "—",
                    system.ownership_status,
                    system.owner_error,
                )
                for system in systems
            ),
        )
        return format_html(
            '<p>{} · Showing up to 50 systems. <a href="{}?project__id__exact={}">View all systems, diagnostics and retry actions</a></p><table><thead><tr><th>System</th><th>Owner</th><th>Snapshot time (UTC)</th><th>Status</th><th>Last error</th></tr></thead><tbody>{}</tbody></table>',
            self.ownership_summary(obj),
            reverse("admin:aasov_plannedsystem_changelist"),
            obj.pk,
            rows,
        )

    @admin.action(description="Capture missing ownership snapshots", permissions=["change"])
    def capture_missing_ownership(self, request, queryset):
        queue_selected_snapshots(self, request, PlannedSystem.objects.filter(project__in=queryset))


def queue_selected_snapshots(model_admin, request, queryset):
    total, failures = 0, 0
    pending = queryset.filter(owner_observed_at__isnull=True)
    by_project = {}
    for project_id, solar_id in pending.values_list("project_id", "solar_system_id"):
        by_project.setdefault(project_id, []).append(solar_id)
    for project_id, ids in by_project.items():
        try:
            total += queue_ownership_snapshot(project_id, ids)
        except Exception:
            failures += 1
    if total:
        model_admin.message_user(
            request,
            f"Queued {total} missing ownership snapshots. Refresh the list to check status; existing snapshots are preserved.",
            messages.SUCCESS,
        )
    elif not failures:
        model_admin.message_user(request, "No missing ownership snapshots selected.", messages.INFO)
    if failures:
        model_admin.message_user(
            request,
            "Could not queue some snapshots. See Last error on the affected systems and check the Celery broker/workers.",
            messages.ERROR,
        )


class OwnershipFilter(admin.SimpleListFilter):
    title = "ownership snapshot"
    parameter_name = "snapshot"

    def lookups(self, request, model_admin):
        return [("captured", "Captured"), ("missing", "Missing"), ("failed", "Last attempt failed")]

    def queryset(self, request, queryset):
        if self.value() == "captured":
            return queryset.filter(owner_observed_at__isnull=False)
        if self.value() == "missing":
            return queryset.filter(owner_observed_at__isnull=True)
        if self.value() == "failed":
            return queryset.filter(owner_observed_at__isnull=True).exclude(owner_error="")
        return queryset


@admin.register(PlannedSystem)
class PlannedSystemAdmin(admin.ModelAdmin):
    list_display = (
        "solar_system",
        "project",
        "owner_name",
        "owner_id",
        "owner_kind",
        "owner_observed_at",
        "snapshot_status",
        "owner_attempted_at",
        "last_error",
    )
    list_filter = ("project", OwnershipFilter, "owner_kind")
    search_fields = ("solar_system__name", "project__name", "owner_name")
    list_select_related = ("solar_system", "project")
    list_per_page = 50
    actions = ("capture_missing_ownership",)
    fields = (
        "project",
        "solar_system",
        "mode",
        "owner_name",
        "owner_id",
        "owner_kind",
        "owner_observed_at",
        "snapshot_status",
        "owner_queued_at",
        "owner_attempted_at",
        "owner_error",
    )
    readonly_fields = fields

    @admin.display(description="Snapshot status")
    def snapshot_status(self, obj):
        return obj.ownership_status

    @admin.display(description="Last error")
    def last_error(self, obj):
        return obj.owner_error[:160]

    def has_module_permission(self, request):
        return self.has_view_permission(request)

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm("aasov.view_project") or request.user.has_perm(
            "aasov.change_project"
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_snapshot_permission(self, request):
        return request.user.has_perm("aasov.change_project")

    @admin.action(description="Capture missing ownership snapshots", permissions=["snapshot"])
    def capture_missing_ownership(self, request, queryset):
        queue_selected_snapshots(self, request, queryset)
