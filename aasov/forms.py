from django import forms
from eve_sde.models import SovereigntyUpgrade

from .models import PlannedSystem, PlannedUpgrade, Project


class CapitalForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ("capital",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["capital"].queryset = self.instance.systems.select_related("solar_system")
        self.fields["capital"].widget.attrs["class"] = "form-select"
        self.fields[
            "capital"
        ].help_text = "Saved for everyone viewing this plan. Clear to hide distance zones."


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

    def __init__(self, *args, project, instance=None, import_destinations_only=False, **kwargs):
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
        if import_destinations_only:
            self.fields["destination"].queryset = self.fields["destination"].queryset.filter(
                mode=PlannedSystem.Mode.IMPORT
            )
            self.fields[
                "destination"
            ].help_text = "Only systems in Import mode are listed. Change the receiving system to Import if it is missing."
        self.fields["amount"].widget.attrs["class"] = "form-control"


class CSVUploadForm(forms.Form):
    csv_file = forms.FileField(
        label="CSV or TSV file",
        help_text="Maximum 2 MiB and 5,000 rows. Extra workforce, power and status columns are ignored.",
    )
    system_column = forms.CharField(
        required=False,
        label="System column (optional)",
        help_text="Automatically detected. Override with a header name or 1-based column number.",
    )
    upgrade_columns = forms.CharField(
        required=False,
        label="Upgrade columns (optional)",
        help_text="Automatically detected. Override with comma-separated header names or column numbers containing upgrade names/IDs.",
    )
    delimiter = forms.ChoiceField(
        required=False,
        choices=[
            ("", "Detect automatically"),
            (",", "Comma"),
            (";", "Semicolon"),
            ("\t", "Tab"),
            ("|", "Pipe"),
        ],
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = (
                "form-select" if isinstance(field.widget, forms.Select) else "form-control"
            )
        self.fields["csv_file"].widget.attrs["accept"] = (
            ".csv,.tsv,text/csv,text/tab-separated-values"
        )

    def clean_csv_file(self):
        from .csv_import import MAX_BYTES

        upload = self.cleaned_data["csv_file"]
        if upload.size > MAX_BYTES:
            raise forms.ValidationError("CSV files must be no larger than 2 MiB.")
        return upload


class CSVConfirmForm(forms.Form):
    import_preview = forms.CharField(widget=forms.HiddenInput)
