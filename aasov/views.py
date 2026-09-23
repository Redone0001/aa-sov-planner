from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from .forms import ModeForm, RouteForm, UpgradeForm
from .models import PlannedSystem, PlannedUpgrade, Project, WorkforceRoute
from .services import (
    calculate_project,
    edit_system,
    remove_item,
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
        }
    )
    return render(request, "aasov/project.html", context)


def is_async(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def saved(request, plan, message):
    if is_async(request):
        context = calculate_project(plan)
        context.update(project=plan, can_edit=request.user.has_perm("aasov.edit_plan"))
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
    form = UpgradeForm(request.POST or None, instance=item)
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
    form = RouteForm(request.POST or None, project=plan, instance=item, initial=initial)
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
    return form_page(request, plan, form, "Workforce route", preview_url=preview_url)


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
