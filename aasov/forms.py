from django import forms
from eve_sde.models import SovereigntyUpgrade

from .models import PlannedSystem, PlannedUpgrade, WorkforceRoute


class StyledForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )


class ModeForm(StyledForm):
    class Meta:
        model = PlannedSystem
        fields = ("mode",)


class UpgradeForm(StyledForm):
    class Meta:
        model = PlannedUpgrade
        fields = ("upgrade", "status")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["upgrade"].queryset = SovereigntyUpgrade.objects.select_related(
            "item_type"
        ).order_by("item_type__name")


class RouteForm(StyledForm):
    class Meta:
        model = WorkforceRoute
        fields = ("source", "destination", "amount")

    def __init__(self, *args, project, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["source"].queryset = project.systems.filter(mode="export").select_related(
            "solar_system"
        )
        self.fields["destination"].queryset = project.systems.filter(mode="import").select_related(
            "solar_system"
        )
        self.fields["amount"].min_value = 1
        self.fields["amount"].widget.attrs["min"] = 1
