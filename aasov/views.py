from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import ModeForm, RouteForm, UpgradeForm
from .models import PlannedSystem, PlannedUpgrade, Project, WorkforceRoute
from .services import (
    calculate_project,
    edit_system,
    remove_item,
    remove_system,
    route_proposal,
    save_route,
    save_upgrade,
)


@login_required
@permission_required("aasov.view_project", raise_exception=True)
def index(request):
    return render(request, "aasov/index.html", {"projects": Project.objects.all()})


@login_required
@permission_required("aasov.view_project", raise_exception=True)
def project(request, project_id):
    plan = get_object_or_404(Project, pk=project_id)
    context = calculate_project(plan)
    context.update(
        {
            "project": plan,
            "projects": Project.objects.all(),
            "can_edit": request.user.has_perm("aasov.edit_plan"),
            "can_manage": request.user.has_perm("aasov.manage_plan"),
        }
    )
    return render(request, "aasov/project.html", context)


def is_async(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


@login_required
@permission_required("aasov.view_project", raise_exception=True)
@require_GET
def map_data(request, project_id):
    from .map_data import project_map

    plan = get_object_or_404(Project.objects.select_related("capital__solar_system"), pk=project_id)
    return JsonResponse(
        project_map(
            plan,
            calculate_project(plan),
            request.user.has_perm("aasov.edit_plan"),
            request.user.has_perm("aasov.manage_plan"),
        )
    )


@login_required
@permission_required("aasov.view_project", raise_exception=True)
@require_GET
def map_range(request, project_id, solar_id):
    from .map_data import nearby_systems
    from .sde import eligible_systems

    plan = get_object_or_404(Project.objects.select_related("capital__solar_system"), pk=project_id)
    source = get_object_or_404(eligible_systems(), pk=solar_id)
    return JsonResponse(nearby_systems(plan, source))


@login_required
@permission_required(("aasov.view_project", "aasov.manage_plan"), raise_exception=True)
@require_http_methods(["GET", "POST"])
def capital(request, project_id):
    from .forms import CapitalForm

    with transaction.atomic():
        plan = get_object_or_404(Project.objects.select_for_update(), pk=project_id)
        form = CapitalForm(request.POST or None, instance=plan)
        if request.method == "POST" and form.is_valid():
            form.save()
            return saved(request, plan, "Plan capital updated. Distance zones recalculated.")
    return form_page(request, plan, form, "Plan capital", hide_budget_note=True)


def saved(request, plan, message):
    if is_async(request):
        context = calculate_project(plan)
        context.update(
            project=plan,
            can_edit=request.user.has_perm("aasov.edit_plan"),
            can_manage=request.user.has_perm("aasov.manage_plan"),
        )
        return JsonResponse(
            {
                "saved": True,
                "message": message,
                "board": render_to_string("aasov/_board.html", context, request=request),
            }
        )
    messages.success(request, message)
    return redirect("aasov:project", project_id=plan.pk)


def form_page(request, plan, form, title, **extra):
    context = {"project": plan, "form": form, "title": title, "action": request.path, **extra}
    if is_async(request):
        return JsonResponse(
            {
                "saved": False,
                "title": title,
                "html": render_to_string("aasov/_form.html", context, request=request),
            }
        )
    return render(request, "aasov/form.html", context)


def validation_error(form, error):
    for message in error.messages:
        form.add_error(None, message)


@login_required
@permission_required(("aasov.view_project", "aasov.edit_plan"), raise_exception=True)
def system_mode(request, project_id, system_id):
    plan = get_object_or_404(Project, pk=project_id)
    system = get_object_or_404(PlannedSystem, pk=system_id, project=plan)
    form = ModeForm(request.POST or None, instance=system)
    if request.method == "POST" and form.is_valid():
        try:
            edit_system(plan.pk, system.pk, form.cleaned_data["mode"])
        except ValidationError as error:
            validation_error(form, error)
        else:
            return saved(request, plan, "Workforce mode updated.")
    return form_page(request, plan, form, f"Workforce mode · {system}")


@login_required
@permission_required(("aasov.view_project", "aasov.edit_plan"), raise_exception=True)
def upgrade(request, project_id, system_id, upgrade_id=None):
    plan = get_object_or_404(Project, pk=project_id)
    system = get_object_or_404(PlannedSystem, pk=system_id, project=plan)
    item = (
        get_object_or_404(PlannedUpgrade, pk=upgrade_id, system=system)
        if upgrade_id
        else PlannedUpgrade(system=system)
    )
    initial = (
        {"status": request.session.get("aasov_upgrade_status", "planned")} if not upgrade_id else {}
    )
    form = UpgradeForm(request.POST or None, instance=item, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            save_upgrade(
                plan.pk,
                system.pk,
                form.cleaned_data["upgrade"],
                form.cleaned_data["status"],
                upgrade_id,
            )
        except ValidationError as error:
            validation_error(form, error)
        except ObjectDoesNotExist as error:
            raise Http404 from error
        else:
            request.session["aasov_upgrade_status"] = form.cleaned_data["status"]
            return saved(request, plan, "Upgrade saved. Budgets updated.")
    return form_page(request, plan, form, f"Upgrade · {system}", repeat=upgrade_id is None)


@login_required
@permission_required(("aasov.view_project", "aasov.edit_plan"), raise_exception=True)
def route(request, project_id, route_id=None):
    plan = get_object_or_404(Project, pk=project_id)
    item = (
        get_object_or_404(WorkforceRoute, pk=route_id, source__project=plan) if route_id else None
    )
    initial = {key: request.GET.get(key) for key in ("source", "destination")}
    import_destinations_only = bool(request.GET.get("source"))
    form = RouteForm(
        request.POST or None,
        project=plan,
        instance=item,
        initial=initial,
        import_destinations_only=import_destinations_only,
    )
    if request.method == "POST" and form.is_valid():
        try:
            save_route(
                plan.pk,
                form.cleaned_data["source"],
                form.cleaned_data["destination"],
                form.cleaned_data["amount"],
                route_id,
                configure_modes=True,
            )
        except ValidationError as error:
            validation_error(form, error)
        except ObjectDoesNotExist as error:
            raise Http404 from error
        else:
            return saved(
                request,
                plan,
                "Workforce route saved. Source set to Export; destination set to Import.",
            )
    preview_url = reverse("aasov:route_preview", args=[plan.pk])
    if route_id:
        preview_url += f"?route_id={route_id}"
    if import_destinations_only:
        preview_url += ("&" if "?" in preview_url else "?") + "imports_only=1"
    return form_page(
        request,
        plan,
        form,
        "Workforce route",
        preview_url=preview_url,
        action=request.get_full_path(),
    )


@login_required
@permission_required(("aasov.view_project", "aasov.edit_plan"), raise_exception=True)
@require_POST
def remove(request, project_id, kind, item_id):
    if kind not in ("upgrade", "route"):
        raise Http404
    plan = get_object_or_404(Project, pk=project_id)
    try:
        remove_item(project_id, kind, item_id)
    except ObjectDoesNotExist as error:
        raise Http404 from error
    return saved(request, plan, "Planning item removed. Budgets updated.")


@login_required
@permission_required(("aasov.view_project", "aasov.edit_plan"), raise_exception=True)
@require_GET
def route_preview(request, project_id):
    plan = get_object_or_404(Project, pk=project_id)
    try:
        source = int(request.GET.get("source", ""))
        destination = int(request.GET.get("destination", ""))
        route_id = int(request.GET["route_id"]) if request.GET.get("route_id") else None
    except (ValueError, TypeError):
        return JsonResponse({"valid": False, "message": "Choose both a source and a destination."})
    if route_id:
        get_object_or_404(WorkforceRoute, pk=route_id, source__project=plan)
    if (
        request.GET.get("imports_only") == "1"
        and not plan.systems.filter(pk=destination, mode=PlannedSystem.Mode.IMPORT).exists()
    ):
        return JsonResponse({"valid": False, "message": "Choose a destination in Import mode."})
    try:
        path = route_proposal(plan.pk, source, destination, route_id)
    except ValidationError as error:
        return JsonResponse({"valid": False, "message": " ".join(error.messages)})
    return JsonResponse(
        {
            "valid": True,
            "message": "Valid stargate path. Connectivity is checked again when saving.",
            "path": [{"name": str(node), "mode": node.get_mode_display()} for node in path],
        }
    )


@login_required
@permission_required(("aasov.view_project", "aasov.manage_plan"), raise_exception=True)
@require_http_methods(["GET", "POST"])
def system_remove(request, project_id, system_id):
    from django import forms

    plan = get_object_or_404(Project, pk=project_id)
    system = get_object_or_404(PlannedSystem, project=plan, pk=system_id)
    if request.method == "POST":
        try:
            remove_system(plan.pk, system.pk)
        except ObjectDoesNotExist as error:
            raise Http404 from error
        return saved(request, plan, f"{system} removed from the plan.")
    return form_page(
        request,
        plan,
        forms.Form(),
        f"Remove {system}?",
        explanation="This removes the system, its upgrades and its import/export routes. Routes passing through it may become invalid. It stays excluded when the project's regions are saved again.",
        submit_label="Remove system",
        hide_budget_note=True,
    )


@login_required
@permission_required(("aasov.view_project", "aasov.edit_plan"), raise_exception=True)
@require_http_methods(["GET", "POST"])
def best_ratting(request, project_id, constellation_id):
    from django import forms
    from django.core import signing

    from .optimizer import apply_proposal, preview_rows, propose

    class ProposalForm(forms.Form):
        proposal = forms.CharField(widget=forms.HiddenInput)

    plan = get_object_or_404(Project, pk=project_id)
    if not plan.systems.filter(solar_system__constellation_id=constellation_id).exists():
        raise Http404
    salt = f"aasov.ratting.{request.user.pk}"
    preview = {}
    form = ProposalForm(request.POST if request.method == "POST" else None)
    try:
        if request.method == "POST":
            if form.is_valid():
                proposal = signing.loads(form.cleaned_data["proposal"], salt=salt, max_age=900)
                if proposal["constellation"] != constellation_id:
                    raise ValidationError("This preview belongs to another constellation.")
                apply_proposal(plan.pk, proposal)
                return saved(
                    request,
                    plan,
                    "Ratting plan applied. Upgrades, modes and workforce routes updated.",
                )
        else:
            proposal = propose(plan, constellation_id)
            preview = preview_rows(plan, proposal)
            form = ProposalForm(
                initial={"proposal": signing.dumps(proposal, salt=salt, compress=True)}
            )
    except signing.BadSignature:
        form.add_error(
            None, "Preview expired or invalid. Close this dialog and run Best ratting again."
        )
    except ValidationError as error:
        if not form.is_bound:
            form = ProposalForm({})
        validation_error(form, error)
    return form_page(
        request,
        plan,
        form,
        "Best ratting · Preview",
        submit_label="Apply ratting plan",
        hide_budget_note=True,
        disable_submit=not preview,
        **preview,
    )


@login_required
@permission_required(("aasov.view_project", "aasov.edit_plan"), raise_exception=True)
@require_POST
def upgrade_installed(request, project_id, system_id, upgrade_id):
    from .services import install_upgrade

    plan = get_object_or_404(Project, pk=project_id)
    try:
        install_upgrade(plan.pk, system_id, upgrade_id)
    except ObjectDoesNotExist as error:
        raise Http404 from error
    except ValidationError as error:
        if is_async(request):
            return JsonResponse({"message": " ".join(error.messages)}, status=409)
        messages.error(request, " ".join(error.messages))
        return redirect("aasov:project", project_id=plan.pk)
    return saved(request, plan, "Upgrade marked as installed (Online).")


@login_required
@permission_required(("aasov.view_project", "aasov.manage_plan"), raise_exception=True)
@require_GET
def csv_template(request, project_id):
    from django.http import HttpResponse

    get_object_or_404(Project, pk=project_id)
    response = HttpResponse(
        "\ufeffsystem,upgrade\r\nYOUR-SYSTEM,Major Threat Detection Array III\r\n",
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = 'attachment; filename="sov-planner-template.csv"'
    return response


@login_required
@permission_required(("aasov.view_project", "aasov.manage_plan"), raise_exception=True)
@require_http_methods(["GET", "POST"])
def csv_upload(request, project_id):
    from django.core import signing

    from .csv_import import apply_import, parse_import
    from .forms import CSVConfirmForm, CSVUploadForm

    plan = get_object_or_404(Project, pk=project_id)
    salt = f"aasov.csv.{request.user.pk}"
    preview = None
    if request.method == "POST" and "import_preview" in request.POST:
        confirmation = CSVConfirmForm(request.POST)
        try:
            if not confirmation.is_valid():
                raise ValidationError("Missing import preview. Upload the CSV again.")
            payload = signing.loads(
                confirmation.cleaned_data["import_preview"], salt=salt, max_age=900
            )
            added, reset = apply_import(plan.pk, payload)
            return saved(
                request,
                plan,
                f"CSV imported: {added} upgrades added as Planned; {reset} existing upgrades reset to Planned.",
            )
        except (signing.BadSignature, ValidationError) as error:
            form = CSVUploadForm({})
            form.add_error(
                None,
                "Preview expired or invalid. Upload the CSV again."
                if isinstance(error, signing.BadSignature)
                else " ".join(error.messages),
            )
    else:
        form = CSVUploadForm(
            request.POST if request.method == "POST" else None, request.FILES or None
        )
        if request.method == "POST" and form.is_valid():
            try:
                preview = parse_import(
                    plan,
                    form.cleaned_data["csv_file"],
                    form.cleaned_data["system_column"],
                    form.cleaned_data["upgrade_columns"],
                    form.cleaned_data["delimiter"],
                )
            except ValidationError as error:
                validation_error(form, error)
            else:
                if not preview["error_count"]:
                    token = signing.dumps(
                        {"project": plan.pk, "entries": preview["entries"]},
                        salt=salt,
                        compress=True,
                    )
                    form = CSVConfirmForm(initial={"import_preview": token})
    ready = preview is not None and not preview["error_count"]
    return form_page(
        request,
        plan,
        form,
        "Import planned upgrades · CSV",
        csv_preview=preview,
        csv_help=True,
        csv_ready=ready,
        submit_label="Import as Planned" if ready else "Preview CSV",
        hide_budget_note=True,
    )
