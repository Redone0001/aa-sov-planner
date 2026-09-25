import pytest
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.urls import reverse

from aasov.models import PlannedUpgrade, Project

pytestmark = pytest.mark.django_db


def grant(user, *codes):
    user.user_permissions.add(
        *Permission.objects.filter(content_type__app_label="aasov", codename__in=codes)
    )
    for key in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        user.__dict__.pop(key, None)


@pytest.mark.parametrize(
    "editor,manager,staff,change,expected",
    [
        (False, False, False, False, {"Public"}),
        (True, False, False, False, {"Public", "Editors", "Editors or Managers"}),
        (False, True, False, False, {"Public", "Managers", "Editors or Managers"}),
        (True, True, False, False, {"Public", "Editors", "Managers", "Editors or Managers"}),
        (False, False, True, False, {"Public"}),
        (False, False, False, True, {"Public"}),
        (False, False, True, True, {"Public", "Admins"}),
    ],
)
def test_role_combinations(reader, editor, manager, staff, change, expected):
    Project.objects.create(name="Public")
    Project.objects.create(name="Editors", restrict_access=True, allow_editors=True)
    Project.objects.create(name="Managers", restrict_access=True, allow_managers=True)
    Project.objects.create(name="Admins", restrict_access=True, allow_admins=True)
    Project.objects.create(
        name="Editors or Managers", restrict_access=True, allow_editors=True, allow_managers=True
    )
    reader.is_staff = staff
    reader.save()
    grant(
        reader,
        *(["edit_plan"] if editor else []),
        *(["manage_plan"] if manager else []),
        *(["change_project"] if change else []),
    )
    assert set(Project.objects.visible_to(reader).values_list("name", flat=True)) == expected


def test_default_visibility_validation_and_superuser(world, django_user_model):
    assert not world.project.restrict_access
    world.project.restrict_access = True
    with pytest.raises(ValidationError, match="at least one"):
        world.project.full_clean()
    world.project.allow_admins = True
    world.project.full_clean()
    world.project.save()
    user = django_user_model.objects.create_superuser("admin", "admin@example.invalid", "test")
    assert Project.objects.visible_to(user).filter(pk=world.project.pk).exists()
    user.is_active = False
    assert not Project.objects.visible_to(user).exists()


def test_hidden_from_index_switcher_and_read_endpoints(world, client, reader):
    system = world.system("Secret system")
    world.project.restrict_access = True
    world.project.allow_managers = True
    world.project.save()
    public = Project.objects.create(name="Public")
    client.force_login(reader)
    for name, args in [("index", []), ("project", [public.pk])]:
        html = client.get(reverse(f"aasov:{name}", args=args)).content.decode()
        assert world.project.name not in html
        assert "Public" in html
    for name, args in [("project", []), ("map_data", []), ("map_range", [system.solar_system_id])]:
        assert (
            client.get(reverse(f"aasov:{name}", args=[world.project.pk, *args])).status_code == 404
        )
    grant(reader, "manage_plan")
    assert client.get(reverse("aasov:project", args=[world.project.pk])).status_code == 200
    assert client.get(reverse("aasov:map_data", args=[world.project.pk])).status_code == 200


def test_restricted_mutation_and_preview_endpoints(world, client, editor):
    system = world.system("Secret system")
    item = PlannedUpgrade.objects.create(system=system, upgrade=world.upgrade())
    grant(editor, "manage_plan")
    world.project.restrict_access = True
    world.project.allow_admins = True
    world.project.save()
    client.force_login(editor)
    endpoints = [
        ("balance_workforce", []),
        ("capital", []),
        ("csv_upload", []),
        ("csv_template", []),
        ("upgrade_installed", [system.pk, item.pk]),
        ("upgrade_offline", [system.pk, item.pk]),
        ("best_ratting", [system.solar_system.constellation_id]),
        ("system_remove", [system.pk]),
        ("mode", [system.pk]),
        ("upgrade_add", [system.pk]),
        ("upgrade_edit", [system.pk, item.pk]),
        ("route_preview", []),
        ("route_add", []),
        ("route_edit", [999]),
        ("remove", ["upgrade", item.pk]),
        ("remove", ["route", 999]),
    ]
    post_only = {"upgrade_installed", "upgrade_offline", "remove"}
    get_only = {"csv_template", "route_preview"}
    for name, args in endpoints:
        url = reverse(f"aasov:{name}", args=[world.project.pk, *args])
        if name not in post_only:
            assert client.get(url).status_code == 404, name
        if name not in get_only:
            assert client.post(url, HTTP_X_REQUESTED_WITH="XMLHttpRequest").status_code == 404, name
    item.refresh_from_db()
    assert item.status == "planned"
    assert world.project.systems.count() == 1


def test_visibility_does_not_grant_edit_and_revocation_blocks_open_page(world, client, reader):
    system = world.system("A")
    world.project.restrict_access = True
    world.project.allow_managers = True
    world.project.save()
    grant(reader, "manage_plan")
    client.force_login(reader)
    assert client.get(reverse("aasov:project", args=[world.project.pk])).status_code == 200
    assert (
        client.post(reverse("aasov:upgrade_add", args=[world.project.pk, system.pk])).status_code
        == 403
    )
    world.project.allow_managers = False
    world.project.allow_admins = True
    world.project.save()
    assert (
        client.post(reverse("aasov:system_remove", args=[world.project.pk, system.pk])).status_code
        == 404
    )
    assert client.get(reverse("aasov:map_data", args=[world.project.pk])).status_code == 404


def test_admin_lists_details_and_snapshot_actions_respect_access(world, client, reader):
    system = world.system("Secret system")
    world.project.restrict_access = True
    world.project.allow_managers = True
    world.project.save()
    reader.is_staff = True
    reader.save()
    grant(reader, "change_project")
    client.force_login(reader)
    for name in ["aasov_project_changelist", "aasov_plannedsystem_changelist"]:
        html = client.get(reverse(f"admin:{name}")).content.decode()
        assert "Secret system" not in html and world.project.name not in html
    # Django admin redirects unknown/filtered object IDs without rendering their data.
    for name, pk in [
        ("aasov_project_change", world.project.pk),
        ("aasov_plannedsystem_change", system.pk),
    ]:
        response = client.get(reverse(f"admin:{name}", args=[pk]))
        assert response.status_code == 302
    world.project.allow_admins = True
    world.project.save()
    response = client.get(reverse("admin:aasov_project_change", args=[world.project.pk]))
    assert response.status_code == 200
    assert b"Restrict project access" in response.content
    assert b"Plan Managers" in response.content
    assert b"Secret system" in response.content


def test_hidden_admin_bulk_actions_ignore_forged_ids(world, client, reader, monkeypatch):
    system = world.system("Hidden")
    world.project.restrict_access = True
    world.project.allow_managers = True
    world.project.save()
    reader.is_staff = True
    reader.save()
    grant(reader, "change_project", "delete_project")
    client.force_login(reader)
    queued = []
    monkeypatch.setattr("aasov.admin.queue_ownership_snapshot", lambda *args: queued.append(args))
    for name, pk in [
        ("aasov_project_changelist", world.project.pk),
        ("aasov_plannedsystem_changelist", system.pk),
    ]:
        client.post(
            reverse(f"admin:{name}"),
            {"action": "capture_missing_ownership", "_selected_action": pk},
        )
    assert not queued
    client.post(
        reverse("admin:aasov_project_changelist"),
        {"action": "delete_selected", "_selected_action": world.project.pk, "post": "yes"},
    )
    assert Project.objects.filter(pk=world.project.pk).exists()
