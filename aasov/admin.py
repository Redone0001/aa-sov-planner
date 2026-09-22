from django import forms
from django.contrib import admin

from .models import Project
from .sde import eligible_systems
from .services import sync_project


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
    list_display = ("name", "updated_at")
    search_fields = ("name",)
    filter_horizontal = ("regions", "selected_systems")
    readonly_fields = ("updated_at",)
    fieldsets = (
        (None, {"fields": ("name", "description", "updated_at")}),
        (
            "Add systems to this project",
            {
                "fields": ("regions", "selected_systems"),
                "description": "Regions add all player-claimable nullsec systems. Selections are combined and deduplicated. Saving adds missing systems without upgrades. Removing selections preserves existing plans.",
            },
        ),
    )

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        sync_project(form.instance)
