import pytest
from bs4 import BeautifulSoup
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from aasov.models import PlannedSystem, PlannedUpgrade, Project

pytestmark = pytest.mark.django_db


def url(name, *args):
    return reverse(f"aasov:{name}", args=args)


def test_anonymous_and_unpermitted_users(client, django_user_model, world):
    assert client.get(url("index")).status_code == 302
    user = django_user_model.objects.create_user("no-access")
    client.force_login(user)
    assert client.get(url("index")).status_code == 403
    assert client.get(url("project", world.project.pk)).status_code == 403


def test_reader_sees_budget_but_cannot_mutate(client, reader, world):
    system = world.system("Alpha")
    client.force_login(reader)
    response = client.get(url("project", world.project.pk))
    assert response.status_code == 200
    assert b"Alpha" in response.content
    assert b"Read only" in response.content
    assert b"Add upgrade" not in response.content
    assert b"progressbar" in response.content
    for name, args in (
        ("mode", [world.project.pk, system.pk]),
        ("upgrade_add", [world.project.pk, system.pk]),
        ("route_add", [world.project.pk]),
        ("remove", [world.project.pk, "upgrade", 1]),
    ):
        assert client.post(url(name, *args), {}).status_code == 403


def test_editor_upgrade_lifecycle_and_negative_warning(client, editor, world):
    system = world.system("Alpha")
    upgrade = world.upgrade("Big upgrade", power_allocation=2000)
    client.force_login(editor)
    response = client.post(
        url("upgrade_add", world.project.pk, system.pk),
        {"upgrade": upgrade.pk, "status": "planned"},
    )
    assert response.status_code == 302
    item = system.upgrades.get()
    response = client.get(url("project", world.project.pk))
    assert b"Power deficit" in response.content and b"text-danger" in response.content
    assert client.get(url("upgrade_edit", world.project.pk, system.pk, item.pk)).status_code == 200
    response = client.post(
        url("upgrade_edit", world.project.pk, system.pk, item.pk),
        {"upgrade": upgrade.pk, "status": "offline"},
    )
    assert response.status_code == 302
    assert b"Power deficit" not in client.get(url("project", world.project.pk)).content
    remove = url("remove", world.project.pk, "upgrade", item.pk)
    assert client.get(remove).status_code == 405
    assert client.post(remove).status_code == 302
    assert not PlannedUpgrade.objects.exists()


def test_cross_project_ids_cannot_be_edited(client, editor, world):
    a = world.system("A")
    other = Project.objects.create(name="Other")
    client.force_login(editor)
    assert client.post(url("mode", other.pk, a.pk), {"mode": "import"}).status_code == 404
    assert client.post(url("upgrade_add", other.pk, a.pk), {}).status_code == 404
    assert client.post(url("remove", other.pk, "upgrade", 9999)).status_code == 404


def test_csrf_required(editor, world):
    a = world.system("A")
    client = Client(enforce_csrf_checks=True)
    client.force_login(editor)
    assert client.post(url("mode", world.project.pk, a.pk), {"mode": "export"}).status_code == 403


def test_route_forms_validate_and_save(client, editor, world):
    a = world.system("A", "export")
    b = world.system("B", "import")
    world.connect(a, b)
    client.force_login(editor)
    endpoint = url("route_add", world.project.pk)
    assert client.get(endpoint).status_code == 200
    assert (
        client.post(endpoint, {"source": a.pk, "destination": b.pk, "amount": 500}).status_code
        == 302
    )
    route = a.export_route
    assert client.get(url("route_edit", world.project.pk, route.pk)).status_code == 200
    response = client.post(url("mode", world.project.pk, a.pk), {"mode": "transit"})
    assert response.status_code == 200 and b"break an existing route" in response.content
    assert client.post(url("remove", world.project.pk, "route", route.pk)).status_code == 302
    assert client.post(url("mode", world.project.pk, a.pk), {"mode": "transit"}).status_code == 302


def test_bad_choice_and_duplicate_are_form_errors(client, editor, world):
    a = world.system("A")
    upgrade = world.upgrade()
    client.force_login(editor)
    endpoint = url("upgrade_add", world.project.pk, a.pk)
    assert client.post(endpoint, {"upgrade": "oops", "status": "planned"}).status_code == 200
    assert client.post(endpoint, {"upgrade": upgrade.pk, "status": "invalid"}).status_code == 200
    assert client.post(endpoint, {"upgrade": upgrade.pk, "status": "planned"}).status_code == 302
    assert client.post(endpoint, {"upgrade": upgrade.pk, "status": "planned"}).status_code == 200
    assert a.upgrades.count() == 1


def test_admin_creates_project_and_expands_region(client, django_user_model, world):
    world.system("A")
    world.system("B")
    admin = django_user_model.objects.create_superuser("admin", "test@example.invalid", "test")
    client.force_login(admin)
    response = client.post(
        reverse("admin:aasov_project_add"),
        {
            "name": "Admin plan",
            "description": "A plan",
            "regions": [world.region.pk],
            "_save": "Save",
        },
    )
    assert response.status_code == 302
    project = Project.objects.get(name="Admin plan")
    assert project.systems.count() == 2
    assert not PlannedUpgrade.objects.filter(system__project=project).exists()


def test_empty_project_selection_admin_error(client, django_user_model):
    admin = django_user_model.objects.create_superuser("admin", "test@example.invalid", "test")
    client.force_login(admin)
    response = client.post(reverse("admin:aasov_project_add"), {"name": "Empty", "_save": "Save"})
    assert response.status_code == 200
    assert not Project.objects.filter(name="Empty").exists()


def test_edit_permission_alone_does_not_grant_read(client, django_user_model, world):
    user = django_user_model.objects.create_user("editonly")
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="aasov", codename="edit_plan")
    )
    client.force_login(user)
    assert client.get(url("project", world.project.pk)).status_code == 403


def test_route_cross_project_and_fourth_source_rejected(client, editor, world):
    dest = world.system("Destination", "import")
    other = Project.objects.create(name="Other")
    outside = PlannedSystem.objects.create(
        project=other, solar_system=dest.solar_system, mode="export"
    )
    client.force_login(editor)
    endpoint = url("route_add", world.project.pk)
    response = client.post(endpoint, {"source": outside.pk, "destination": dest.pk, "amount": 10})
    assert response.status_code == 200
    for i in range(4):
        source = world.system(str(i), "export")
        world.connect(source, dest)
        response = client.post(
            endpoint, {"source": source.pk, "destination": dest.pk, "amount": 10}
        )
        assert response.status_code == (302 if i < 3 else 200)
    assert dest.import_routes.count() == 3


@pytest.mark.parametrize(
    ("theme", "hook"),
    [("flatly", "FlatlyThemeHook"), ("darkly", "DarklyThemeHook"), ("materia", "MateriaThemeHook")],
)
def test_aa_themes_render_planner_and_editor_forms(client, editor, world, theme, hook):
    system = world.system("Theme test")
    upgrade = world.upgrade("Over budget", power_allocation=2000)
    PlannedUpgrade.objects.create(system=system, upgrade=upgrade)
    client.force_login(editor)
    session = client.session
    session["THEME"] = f"allianceauth.theme.{theme}.auth_hooks.{hook}"
    session.save()
    for endpoint in [
        url("project", world.project.pk),
        url("upgrade_add", world.project.pk, system.pk),
        url("route_add", world.project.pk),
        url("mode", world.project.pk, system.pk),
    ]:
        response = client.get(endpoint)
        assert response.status_code == 200
        html = response.content.decode()
        assert BeautifulSoup(html, "html.parser").html["data-theme"] == theme
        assert f"/{theme}/bootstrap.min.css" in html
        assert "/static/aasov/planner.css" in html
    html = client.get(url("project", world.project.pk)).content.decode()
    assert "Power deficit" in html and 'role="progressbar"' in html
