import pytest
from bs4 import BeautifulSoup
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from aasov.models import PlannedSystem, Project, WorkforceRoute
from aasov.services import calculate_project, route_proposal, save_route

pytestmark = pytest.mark.django_db
AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def endpoint(name, *args):
    return reverse(f"aasov:{name}", args=args)


def test_async_upgrade_add_errors_edit_delete(client, editor, world):
    a = world.system("Alpha")
    upgrade = world.upgrade(power_allocation=2000)
    client.force_login(editor)
    url = endpoint("upgrade_add", world.project.pk, a.pk)
    form = client.get(url, **AJAX).json()
    assert "<form" in form["html"] and "<html" not in form["html"]
    response = client.post(url, {"upgrade": upgrade.pk, "status": "planned"}, **AJAX)
    assert response.status_code == 200
    data = response.json()
    assert data["saved"] and "Power deficit" in data["board"]
    assert "<html" not in data["board"]
    bad = client.post(url, {"upgrade": upgrade.pk, "status": "planned"}, **AJAX).json()
    assert not bad["saved"] and "already exists" in bad["html"]
    item = a.upgrades.get()
    updated = client.post(
        endpoint("upgrade_edit", world.project.pk, a.pk, item.pk),
        {"upgrade": upgrade.pk, "status": "offline"},
        **AJAX,
    ).json()
    assert updated["saved"] and "Power deficit" not in updated["board"]
    removed = client.post(endpoint("remove", world.project.pk, "upgrade", item.pk), **AJAX).json()
    assert removed["saved"] and not a.upgrades.exists()


def test_route_preview_and_save_with_automatic_modes(client, editor, world):
    a, t, b = world.system("Source"), world.system("Transit"), world.system("Destination")
    world.connect(a, t)
    world.connect(t, b)
    client.force_login(editor)
    params = {"source": a.pk, "destination": b.pk}
    preview = client.get(endpoint("route_preview", world.project.pk), params).json()
    assert preview["valid"]
    assert [p["name"] for p in preview["path"]] == ["Source", "Transit", "Destination"]
    assert [p["mode"] for p in preview["path"]] == ["Export", "Transit", "Import"]
    assert not WorkforceRoute.objects.exists()
    a.refresh_from_db()
    assert a.mode == "transit"  # Preview is read-only.
    response = client.post(
        endpoint("route_add", world.project.pk), {**params, "amount": 800}, **AJAX
    ).json()
    assert response["saved"] and "Imports 800 from" in response["board"]
    assert "Exports 800 to" in response["board"]
    a.refresh_from_db()
    b.refresh_from_db()
    t.refresh_from_db()
    assert (a.mode, t.mode, b.mode) == ("export", "transit", "import")
    budgets = {x.system.pk: x for x in calculate_project(world.project)["budgets"]}
    assert budgets[t.pk].imported == 0 and budgets[t.pk].transiting == 800
    assert budgets[b.pk].imports[0]["route"].source_id == a.pk


def test_invalid_path_preview_and_save_leave_modes_unchanged(client, editor, world):
    a, b = world.system("A"), world.system("B")
    client.force_login(editor)
    values = {"source": a.pk, "destination": b.pk, "amount": 100}
    assert not client.get(endpoint("route_preview", world.project.pk), values).json()["valid"]
    response = client.post(endpoint("route_add", world.project.pk), values, **AJAX).json()
    assert not response["saved"] and "No valid stargate path" in response["html"]
    assert set(world.project.systems.values_list("mode", flat=True)) == {"transit"}
    assert not WorkforceRoute.objects.exists()


def test_save_rechecks_path_after_preview(client, editor, world):
    a, t, b = world.system("A"), world.system("T"), world.system("B")
    world.connect(a, t)
    world.connect(t, b)
    client.force_login(editor)
    values = {"source": a.pk, "destination": b.pk, "amount": 100}
    assert client.get(endpoint("route_preview", world.project.pk), values).json()["valid"]
    t.mode = "import"
    t.save()
    assert not client.post(endpoint("route_add", world.project.pk), values, **AJAX).json()["saved"]
    a.refresh_from_db()
    b.refresh_from_db()
    assert a.mode == b.mode == "transit"


def test_automatic_modes_protect_existing_transit_routes(world):
    a, t, b, d = [world.system(n) for n in ("A", "T", "B", "D")]
    world.connect(a, t)
    world.connect(t, b)
    world.connect(t, d)
    save_route(world.project.pk, a, b, 100, configure_modes=True)
    with pytest.raises(ValidationError, match="break an existing route"):
        save_route(world.project.pk, t, d, 100, configure_modes=True)
    t.refresh_from_db()
    d.refresh_from_db()
    assert t.mode == d.mode == "transit"
    assert WorkforceRoute.objects.count() == 1


def test_preview_enforces_three_sources_one_destination_and_edit(world):
    b = world.system("B")
    sources = [world.system(f"A{i}") for i in range(4)]
    for a in sources:
        world.connect(a, b)
    for a in sources[:3]:
        save_route(world.project.pk, a, b, 10, configure_modes=True)
    with pytest.raises(ValidationError, match="three source"):
        route_proposal(world.project.pk, sources[3].pk, b.pk)
    with pytest.raises(ValidationError, match="already exports"):
        route_proposal(world.project.pk, sources[0].pk, b.pk)
    route = WorkforceRoute.objects.get(source=sources[0])
    assert route_proposal(world.project.pk, sources[0].pk, b.pk, route.pk)
    save_route(world.project.pk, sources[0], b, 20, route.pk, configure_modes=True)
    assert WorkforceRoute.objects.get(pk=route.pk).amount == 20


def test_preview_and_async_forms_require_editor_and_csrf(client, reader, world):
    a = world.system("A")
    client.force_login(reader)
    assert client.get(endpoint("route_preview", world.project.pk)).status_code == 403
    assert client.get(endpoint("upgrade_add", world.project.pk, a.pk), **AJAX).status_code == 403


def test_async_csrf_and_scoping(editor, world):
    a = world.system("A")
    client = Client(enforce_csrf_checks=True)
    client.force_login(editor)
    assert (
        client.post(
            endpoint("mode", world.project.pk, a.pk), {"mode": "export"}, **AJAX
        ).status_code
        == 403
    )
    other = Project.objects.create(name="Other")
    outside = PlannedSystem.objects.create(project=other, solar_system=a.solar_system)
    preview = client.get(
        endpoint("route_preview", world.project.pk), {"source": a.pk, "destination": outside.pk}
    ).json()
    assert not preview["valid"]
    assert client.get(endpoint("upgrade_add", other.pk, a.pk), **AJAX).status_code == 404


def test_import_action_prefills_destination(client, editor, world):
    a = world.system("A")
    client.force_login(editor)
    data = client.get(endpoint("route_add", world.project.pk), {"destination": a.pk}, **AJAX).json()
    html = BeautifulSoup(data["html"], "html.parser")
    assert html.select_one("#id_destination option[selected]")["value"] == str(a.pk)
    assert html.select_one("form[data-route-preview]")


def test_export_action_only_lists_importers_and_rechecks_on_save(client, editor, world):
    source = world.system("Source", "export")
    importer = world.system("Importer", "import")
    world.system("Transit")
    world.system("Other exporter", "export")
    world.connect(source, importer)
    client.force_login(editor)
    action = endpoint("route_add", world.project.pk) + f"?source={source.pk}"
    html = BeautifulSoup(client.get(action, **AJAX).json()["html"], "html.parser")
    options = html.select("#id_destination option[value]")
    assert [o["value"] for o in options if o["value"]] == [str(importer.pk)]
    assert html.select_one("form")["action"] == action
    importer.mode = "transit"
    importer.save()
    preview = client.get(
        endpoint("route_preview", world.project.pk),
        {"source": source.pk, "destination": importer.pk, "imports_only": "1"},
    ).json()
    assert not preview["valid"]
    assert not client.post(
        action, {"source": source.pk, "destination": importer.pk, "amount": 10}, **AJAX
    ).json()["saved"]
    importer.mode = "import"
    importer.save()
    assert client.post(
        action, {"source": source.pk, "destination": importer.pk, "amount": 10}, **AJAX
    ).json()["saved"]
