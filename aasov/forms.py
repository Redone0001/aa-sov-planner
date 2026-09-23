from django import forms
from eve_sde.models import SovereigntyUpgrade

from .models import PlannedSystem, PlannedUpgrade


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


class RouteForm(forms.Form):
    source = forms.ModelChoiceField(
        queryset=PlannedSystem.objects.none(), label="Source system (exports workforce)"
    )
    destination = forms.ModelChoiceField(
        queryset=PlannedSystem.objects.none(), label="Destination system (imports workforce)"
    )
    amount = forms.IntegerField(min_value=1, max_value=2147483647, label="Workforce to transfer")

    def __init__(self, *args, project, instance=None, **kwargs):
        if instance:
            kwargs["initial"] = {
                "source": instance.source_id,
                "destination": instance.destination_id,
                "amount": instance.amount,
            }
        super().__init__(*args, **kwargs)
        for name in ("source", "destination"):
            self.fields[name].queryset = project.systems.select_related("solar_system")
            self.fields[name].widget.attrs["class"] = "form-select"
        self.fields["amount"].widget.attrs["class"] = "form-control"
