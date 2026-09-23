import csv
import io

import pytest
from bs4 import BeautifulSoup
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from aasov.csv_import import MAX_BYTES, apply_import, parse_import
from aasov.models import PlannedUpgrade, Project
from aasov.services import calculate_project, save_upgrade

pytestmark = pytest.mark.django_db
AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def upload(text, encoding="utf-8-sig"):
    return SimpleUploadedFile("plan.csv", text.encode(encoding), content_type="text/csv")


def url(name, *args):
    return reverse(f"aasov:{name}", args=args)


@pytest.fixture
def manager(reader):
    reader.user_permissions.add(
        Permission.objects.get(content_type__app_label="aasov", codename="manage_plan")
    )
    return reader


def test_installed_check_is_editor_only_scoped_post_and_idempotent(world, reader, client):
    a = world.system("A")
    u = world.upgrade("Upgrade", power_allocation=10, fuel_item_type=None)
    p = save_upgrade(world.project.pk, a.pk, u, "planned")
    endpoint = url("upgrade_installed", world.project.pk, a.pk, p.pk)
    client.force_login(reader)
    assert client.post(endpoint).status_code == 403
    assert b"sov-installed" not in client.get(url("project", world.project.pk)).content
    reader.user_permissions.add(
        Permission.objects.get(content_type__app_label="aasov", codename="edit_plan")
    )
    assert client.get(endpoint).status_code == 405
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(reader)
    assert secure.post(endpoint).status_code == 403
    other = Project.objects.create(name="Other")
    assert client.post(url("upgrade_installed", other.pk, a.pk, p.pk)).status_code == 404
    before = calculate_project(world.project)["budgets"][0].power_left
    assert client.post(endpoint, **AJAX).json()["saved"]
    assert client.post(endpoint, **AJAX).json()["saved"]
    p.refresh_from_db()
    assert p.status == "online"
    assert calculate_project(world.project)["budgets"][0].power_left == before
    p.status = "offline"
    p.save()
    assert client.post(endpoint, **AJAX).status_code == 409
    p.refresh_from_db()
    assert p.status == "offline"


@pytest.mark.parametrize("delimiter", [",", ";", "\t", "|"])
@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp1252"])
def test_long_csv_dialects_encodings_and_ignored_resource_columns(world, delimiter, encoding):
    a = world.system("A")
    u = world.upgrade("Major Threat Detection Array III")
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter)
    writer.writerow(["Solar System", "Upgrade", "Workforce", "Power", "Status"])
    writer.writerow([" a ", "Major Threat 3", "bad resource", "999", "online"])
    preview = parse_import(world.project, upload(out.getvalue(), encoding))
    assert not preview["errors"] and preview["count"] == 1
    assert preview["ignored"] == ["Workforce", "Power", "Status"]
    assert not PlannedUpgrade.objects.exists()
    apply_import(world.project.pk, {"project": world.project.pk, "entries": preview["entries"]})
    assert a.upgrades.get(upgrade=u).status == "planned"


def test_multi_columns_lists_wide_markers_and_tiers(world):
    a = world.system("A")
    major = world.upgrade("Major Threat Detection Array III")
    minor = world.upgrade("Minor Threat Detection Array II")
    variants = [
        "System,Upgrade 1,Upgrade 2,Power\nA,Major Threat 3,Minor Threat II,100\n",
        'System,Upgrades\nA,"Major Threat III;Minor Threat 2"\n',
        'System,Upgrades\nA,"Major Threat III,Minor Threat 2"\n',
        "System,Major Threat Detection Array III,Minor Threat Detection Array II,Power\nA,yes,x,999\n",
        "System,Major Threat,Minor Threat Detection Array,Power\nA,III,2,999\n",
    ]
    for text in variants:
        preview = parse_import(world.project, upload(text))
        assert not preview["errors"], preview
        assert {(e["system"], e["upgrade"]) for e in preview["entries"]} == {
            (a.pk, major.pk),
            (a.pk, minor.pk),
        }


def test_ids_custom_mapping_preamble_and_excel_sep(world):
    a = world.system("A")
    u = world.upgrade("Upgrade")
    text = f"sep=;\nPlanner export\nDestination;Module;Power\n{a.solar_system_id};{u.pk};100\n"
    preview = parse_import(
        world.project, upload(text), system_column="Destination", upgrade_columns="2"
    )
    assert preview["count"] == 1 and not preview["errors"]
    text = f"System,system_id,upgrade\nWrong display name,{a.solar_system_id},{u.pk}\n"
    preview = parse_import(world.project, upload(text))
    assert preview["count"] == 1 and not preview["errors"]


def test_duplicates_existing_states_and_stale_import_are_atomic(world):
    a = world.system("A")
    u = world.upgrade("Upgrade")
    second = world.upgrade("Other")
    p = save_upgrade(world.project.pk, a.pk, u, "online")
    text = "System,Upgrade,Status\nA,Upgrade,online\nA,Upgrade,offline\nA,Other,online\n"
    preview = parse_import(world.project, upload(text))
    assert preview["duplicates"] == 1 and preview["resets"] == 1
    assert preview["rows"][0]["action"] == "Online → Planned"
    p.status = "offline"
    p.save()
    with pytest.raises(ValidationError, match="changed"):
        apply_import(world.project.pk, {"project": world.project.pk, "entries": preview["entries"]})
    assert not a.upgrades.filter(upgrade=second).exists()
    preview = parse_import(world.project, upload(text))
    assert apply_import(
        world.project.pk, {"project": world.project.pk, "entries": preview["entries"]}
    ) == (1, 1)
    assert set(a.upgrades.values_list("status", flat=True)) == {"planned"}


def test_unknown_and_ambiguous_upgrade_are_reported_without_guessing(world):
    world.system("A")
    world.upgrade("Upgrade")
    world.upgrade("Major Threat Detection Array III")
    world.upgrade("Major Threat 3")
    preview = parse_import(
        world.project,
        upload("System,Upgrade\nA,Upgrade\nOutside,Upgrade\nA,Typo\nA,Major Threat 3\n"),
    )
    assert preview["count"] == 1 and preview["error_count"] == 3
    assert any("Ambiguous" in e for e in preview["errors"])
    assert not PlannedUpgrade.objects.exists()


def test_csv_preview_upload_apply_permissions_and_template(world, reader, client):
    a = world.system("A")
    world.upgrade("Upgrade")
    client.force_login(reader)
    endpoint = url("csv_upload", world.project.pk)
    assert client.get(endpoint).status_code == 403
    reader.user_permissions.add(
        Permission.objects.get(content_type__app_label="aasov", codename="edit_plan")
    )
    assert client.post(endpoint).status_code == 403  # Editor does not imply Plan Manager.
    reader.user_permissions.add(
        Permission.objects.get(content_type__app_label="aasov", codename="manage_plan")
    )
    assert client.get(endpoint, **AJAX).status_code == 200
    response = client.get(url("csv_template", world.project.pk))
    assert response["Content-Disposition"].startswith("attachment")
    assert "system,upgrade" in response.content.decode("utf-8-sig")
    response = client.post(endpoint, {"csv_file": upload("System,Upgrade\nA,Upgrade\n")}, **AJAX)
    html = response.json()["html"]
    assert 'enctype="multipart/form-data"' in html
    assert not PlannedUpgrade.objects.exists()
    token = BeautifulSoup(html, "html.parser").select_one('[name="import_preview"]')["value"]
    assert client.post(endpoint, {"import_preview": token}, **AJAX).json()["saved"]
    assert a.upgrades.get().status == "planned"
    assert (
        "expired or invalid"
        in client.post(endpoint, {"import_preview": "tampered"}, **AJAX).json()["html"]
    )


def test_invalid_upload_cannot_be_applied_and_csv_csrf_is_required(world, manager, client):
    world.system("A")
    world.upgrade("Upgrade")
    client.force_login(manager)
    endpoint = url("csv_upload", world.project.pk)
    response = client.post(
        endpoint, {"csv_file": upload("System,Upgrade\nA,Upgrade\nMissing,Upgrade\n")}, **AJAX
    )
    html = response.json()["html"]
    assert "nothing will be imported" in html and 'name="import_preview"' not in html
    assert not PlannedUpgrade.objects.exists()
    secure = Client(enforce_csrf_checks=True)
    secure.force_login(manager)
    assert (
        secure.post(endpoint, {"csv_file": upload("System,Upgrade\nA,Upgrade\n")}).status_code
        == 403
    )


def test_file_limits_and_binary_workbook_rejection(world):
    with pytest.raises(ValidationError, match="2 MiB"):
        parse_import(world.project, SimpleUploadedFile("big.csv", b"x" * (MAX_BYTES + 1)))
    with pytest.raises(ValidationError, match="Export the workbook"):
        parse_import(world.project, SimpleUploadedFile("book.xlsx", b"PK\x03\x04other"))
    with pytest.raises(ValidationError):
        parse_import(world.project, upload(""))


def test_csv_preview_cannot_be_applied_to_another_project(world, manager, client):
    a = world.system("A")
    world.upgrade("Upgrade")
    other = Project.objects.create(name="Other")
    client.force_login(manager)
    html = client.post(
        url("csv_upload", world.project.pk),
        {"csv_file": upload("System,Upgrade\nA,Upgrade\n")},
        **AJAX,
    ).json()["html"]
    token = BeautifulSoup(html, "html.parser").select_one('[name="import_preview"]')["value"]
    result = client.post(url("csv_upload", other.pk), {"import_preview": token}, **AJAX).json()
    assert not result["saved"] and "Invalid import preview" in result["html"]
    a.delete()
    result = client.post(
        url("csv_upload", world.project.pk), {"import_preview": token}, **AJAX
    ).json()
    assert not result["saved"] and "changed since" in result["html"]
    assert not PlannedUpgrade.objects.exists()


def test_csv_row_limit_and_error_html_escaping(world, manager, client):
    world.system("A")
    world.upgrade("Upgrade")
    with pytest.raises(ValidationError, match="5,000"):
        parse_import(world.project, upload("System,Upgrade\n" + "A,Upgrade\n" * 5001))
    client.force_login(manager)
    html = client.post(
        url("csv_upload", world.project.pk),
        {"csv_file": upload("System,Upgrade\nA,<script>alert(1)</script>\n")},
        **AJAX,
    ).json()["html"]
    assert "&lt;script&gt;" in html and "<script>" not in html
