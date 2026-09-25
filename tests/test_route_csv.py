import csv
import io

import pytest
from bs4 import BeautifulSoup
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from aasov.csv_import import parse_import as parse_upgrades
from aasov.models import PlannedUpgrade, WorkforceRoute
from aasov.route_csv import apply_import, parse_import
from aasov.services import save_route

pytestmark = pytest.mark.django_db
AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def upload(text):
    return SimpleUploadedFile("routes.csv", text.encode("utf-8-sig"), content_type="text/csv")


def test_route_roundtrip_and_upgrade_export(world, reader, client):
    a, t, b = [world.system(name) for name in ("Source", "Transit", "Destination")]
    world.connect(a, t)
    world.connect(t, b)
    route = save_route(world.project.pk, a, b, 100, configure_modes=True)
    item = PlannedUpgrade.objects.create(
        system=a, upgrade=world.upgrade(name="Name, with comma"), status="temporary"
    )
    client.force_login(reader)
    response = client.get(reverse("aasov:csv_export", args=[world.project.pk]))
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows == [["system", "upgrade", "status"], ["Source", "Name, with comma", "temporary"]]
    assert parse_upgrades(world.project, io.BytesIO(response.content))["count"] == 1
    response = client.get(reverse("aasov:route_csv_export", args=[world.project.pk]))
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[1] == ["Source", "Destination", "100", "Source > Transit > Destination"]
    proposal, preview = parse_import(world.project, io.BytesIO(response.content))
    assert preview["ignored"] == ["path"]
    assert WorkforceRoute.objects.get().pk == route.pk
    assert apply_import(world.project.pk, proposal) == (0, 1)
    assert WorkforceRoute.objects.get().pk == route.pk
    item.refresh_from_db()
    assert item.status == "temporary"


def test_aliases_ids_duplicates_and_extra_columns(world):
    a, b = world.system("Source"), world.system("Destination")
    world.connect(a, b)
    proposal, preview = parse_import(
        world.project,
        upload(
            f"Title row\nFrom;To;Amount;Power\n{a.solar_system_id};destination;100;42\nsource;Destination;100;99\n"
        ),
    )
    assert preview["duplicates"] == 1 and preview["ignored"] == ["Power"]
    assert not WorkforceRoute.objects.exists()
    assert apply_import(world.project.pk, proposal) == (1, 0)
    assert WorkforceRoute.objects.get().amount == 100


def test_merged_batch_allows_final_valid_destination_swap(world):
    sources = [world.system(n) for n in ("A", "B", "C", "D", "E", "F")]
    x, y = world.system("X"), world.system("Y")
    for s in sources:
        world.connect(s, x)
        world.connect(s, y)
    for i, s in enumerate(sources):
        save_route(world.project.pk, s, x if i < 3 else y, 10, configure_modes=True)
    proposal, _ = parse_import(
        world.project, upload("source,destination,workforce\nA,Y,20\nD,X,30\n")
    )
    apply_import(world.project.pk, proposal)
    assert WorkforceRoute.objects.filter(destination=x).count() == 3
    assert WorkforceRoute.objects.filter(destination=y).count() == 3
    assert WorkforceRoute.objects.get(source=sources[1]).amount == 10


@pytest.mark.parametrize(
    "rows",
    [
        "A,B,0",
        "A,B,-1",
        "A,B,1.5",
        "A,B,2147483648",
        "A,B,=1+1",
        "Unknown,B,10",
        "A,A,10",
        "A,B,10\nA,C,10",
        "A,B,10\nA,B,20",
        "A,B,10\nB,C,10",
        "A,B,10\nC,B,10\nD,B,10\nE,B,10",
    ],
)
def test_invalid_file_never_changes_plan(world, rows):
    systems = [world.system(n) for n in "ABCDE"]
    for a in systems:
        for b in systems:
            if a != b:
                world.connect(a, b)
    with pytest.raises(ValidationError):
        parse_import(world.project, upload("source,destination,workforce\n" + rows + "\n"))
    assert not WorkforceRoute.objects.exists()
    assert all(n.mode == "transit" for n in world.project.systems.all())


def test_imported_endpoint_cannot_be_used_as_transit(world):
    a, t, b, x = [world.system(n) for n in ("A", "T", "B", "X")]
    world.connect(a, t)
    world.connect(t, b)
    world.connect(t, x)
    with pytest.raises(ValidationError, match="No valid path"):
        parse_import(world.project, upload("source,destination,workforce\nA,B,10\nT,X,10\n"))


def test_stale_preview_rejected_atomically(world):
    a, b = world.system("A"), world.system("B")
    world.connect(a, b)
    proposal, _ = parse_import(world.project, upload("source,destination,workforce\nA,B,10\n"))
    world.project.systems.filter(pk=b.pk).update(mode="export")
    with pytest.raises(ValidationError, match="changed"):
        apply_import(world.project.pk, proposal)
    assert not WorkforceRoute.objects.exists()


def test_preview_reports_both_budget_deficits_and_overexport(world):
    a, b = world.system("A", workforce=100), world.system("B", workforce=100)
    world.connect(a, b)
    PlannedUpgrade.objects.create(
        system=a, upgrade=world.upgrade(workforce_allocation=50), status="temporary"
    )
    _, preview = parse_import(world.project, upload("source,destination,workforce\nA,B,120\n"))
    row = next(r for r in preview["deficits"] if r["system"].pk == a.pk)
    assert (row["planned"], row["current"], row["overexport"]) == (-20, -70, True)


def test_import_permissions_signature_preview_and_apply(world, client, editor):
    a, b = world.system("A"), world.system("B")
    world.connect(a, b)
    url = reverse("aasov:route_csv_upload", args=[world.project.pk])
    client.force_login(editor)
    assert client.get(url).status_code == 403
    editor.user_permissions.add(
        Permission.objects.get(content_type__app_label="aasov", codename="manage_plan")
    )
    response = client.post(
        url, {"csv_file": upload("source,destination,workforce\nA,B,25\n")}, **AJAX
    )
    assert response.status_code == 200
    form = BeautifulSoup(response.json()["html"], "html.parser")
    token = form.select_one('[name="route_preview"]')["value"]
    assert not WorkforceRoute.objects.exists()
    assert not client.post(url, {"route_preview": token + "x"}, **AJAX).json()["saved"]
    assert client.post(url, {"route_preview": token}, **AJAX).json()["saved"]
    assert WorkforceRoute.objects.get().amount == 25


def test_exports_respect_simplified_view_and_project_restrictions(world, reader, client):
    a = world.system("A")
    for name, status, production in [
        ("Visible", "online", 0),
        ("Planned", "planned", 0),
        ("Booster", "temporary", 100),
    ]:
        PlannedUpgrade.objects.create(
            system=a,
            upgrade=world.upgrade(name=name, workforce_production=production),
            status=status,
        )
    world.project.simplified_viewers = True
    world.project.save()
    client.force_login(reader)
    url = reverse("aasov:csv_export", args=[world.project.pk])
    text = client.get(url).content.decode("utf-8-sig")
    assert "Visible" in text and "Planned" not in text and "Booster" not in text
    assert client.get(reverse("aasov:route_csv_export", args=[world.project.pk])).status_code == 403
    world.project.restrict_access = True
    world.project.allow_editors = True
    world.project.save()
    assert client.get(url).status_code == 404
    assert client.get(reverse("aasov:route_csv_export", args=[world.project.pk])).status_code == 404
