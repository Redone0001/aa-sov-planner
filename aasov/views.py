from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import ModeForm, RouteForm, UpgradeForm
from .models import PlannedSystem, PlannedUpgrade, Project, WorkforceRoute
from .services import calculate_project, edit_system, remove_item, save_route, save_upgrade


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


def form_page(request, plan, form, title):
    return render(request, "aasov/form.html", {"project": plan, "form": form, "title": title})


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
            messages.success(request, "Workforce mode updated.")
            return redirect("aasov:project", project_id=plan.pk)
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
            messages.success(
                request, "Upgrade saved. Planned and online upgrades count toward the budget."
            )
            return redirect("aasov:project", project_id=plan.pk)
    return form_page(request, plan, form, f"Upgrade · {system}")


@login_required
@permission_required(("aasov.view_project", "aasov.edit_plan"), raise_exception=True)
def route(request, project_id, route_id=None):
    plan = get_object_or_404(Project, pk=project_id)
    item = (
        get_object_or_404(WorkforceRoute, pk=route_id, source__project=plan) if route_id else None
    )
    form = RouteForm(request.POST or None, project=plan, instance=item)
    if request.method == "POST" and form.is_valid():
        try:
            save_route(
                plan.pk,
                form.cleaned_data["source"],
                form.cleaned_data["destination"],
                form.cleaned_data["amount"],
                route_id,
            )
        except ValidationError as error:
            validation_error(form, error)
        except ObjectDoesNotExist as error:
            raise Http404 from error
        else:
            messages.success(request, "Workforce route saved.")
            return redirect("aasov:project", project_id=plan.pk)
    return form_page(request, plan, form, "Workforce route")


@login_required
@permission_required(("aasov.view_project", "aasov.edit_plan"), raise_exception=True)
@require_POST
def remove(request, project_id, kind, item_id):
    if kind not in ("upgrade", "route"):
        raise Http404
    get_object_or_404(Project, pk=project_id)
    try:
        remove_item(project_id, kind, item_id)
    except ObjectDoesNotExist as error:
        raise Http404 from error
    messages.success(request, "Planning item removed.")
    return redirect("aasov:project", project_id=project_id)
