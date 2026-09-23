import json

import pytest
from bs4 import BeautifulSoup
from django.urls import reverse

from aasov.models import PlannedUpgrade, Project
from aasov.services import calculate_project

pytestmark = pytest.mark.django_db


def test_inventory_totals_and_inline_install(client, editor, world):
    upgrade = world.upgrade(name="Major Threat Detection Array III")
    items = []
    for i, status in enumerate(("planned", "planned", "online", "offline")):
        items.append(
            PlannedUpgrade.objects.create(
                system=world.system(f"System {i}"), upgrade=upgrade, status=status
            )
        )
    other = world.system("Other project")
    other.project = Project.objects.create(name="Unrelated")
    other.save()
    PlannedUpgrade.objects.create(system=other, upgrade=upgrade, status="planned")
    context = calculate_project(world.project)
    assert context["inventory"] == [{"name": upgrade.item_type.name, "planned": 2, "all": 4}]
    assert context["inventory_clipboard"] == {
        "planned": "Major Threat Detection Array III\t2",
        "all": "Major Threat Detection Array III\t4",
    }
    client.force_login(editor)
    item = items[0]
    result = client.post(
        reverse("aasov:upgrade_installed", args=[world.project.pk, item.system_id, item.pk]),
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    ).json()
    soup = BeautifulSoup(result["board"], "html.parser")
    data = json.loads(soup.select_one("#sov-inventory-clipboard").string)
    assert data["planned"] == "Major Threat Detection Array III\t1"
    assert data["all"] == "Major Threat Detection Array III\t4"


def test_reader_can_view_empty_inventory(client, reader, world):
    client.force_login(reader)
    response = client.get(reverse("aasov:project", args=[world.project.pk]))
    assert response.status_code == 200
    soup = BeautifulSoup(response.content, "html.parser")
    assert len(soup.select("[data-sov-copy]")) == 2
    assert json.loads(soup.select_one("#sov-inventory-clipboard").string) == {
        "planned": "",
        "all": "",
    }
