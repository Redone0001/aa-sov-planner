import itertools

import pytest
from django.contrib.auth.models import Permission
from eve_sde.models import (
    Constellation,
    ItemType,
    Planet,
    PlanetResource,
    Region,
    SolarSystem,
    SovereigntyUpgrade,
    Star,
    Stargate,
    StarResource,
)

from aasov.models import PlannedSystem, Project


@pytest.fixture
def world(db):
    ids = itertools.count(30000001)
    item_ids = itertools.count(80000)
    region = Region.objects.create(id=10000001, name="Test Region")
    constellation = Constellation.objects.create(
        id=20000001, name="Test Constellation", region=region
    )
    project = Project.objects.create(name="Test plan")

    class World:
        def system(self, name, mode="transit", power=1000, workforce=10000, **kwargs):
            pk = next(ids)
            solar = SolarSystem.objects.create(
                id=pk, name=name, constellation=constellation, security_status=-0.5, **kwargs
            )
            star = Star.objects.create(id=pk + 100000000, solar_system=solar)
            StarResource.objects.create(star=star, power=500, workforce=0)
            planet = Planet.objects.create(id=pk + 200000000, name=f"{name} I", solar_system=solar)
            PlanetResource.objects.create(planet=planet, power=power - 500, workforce=workforce)
            return PlannedSystem.objects.create(project=project, solar_system=solar, mode=mode)

        def connect(self, a, b):
            Stargate.objects.create(
                id=next(item_ids), solar_system=a.solar_system, destination=b.solar_system
            )

        def upgrade(self, name="Test upgrade", **kwargs):
            item = ItemType.objects.create(id=next(item_ids), name=name)
            return SovereigntyUpgrade.objects.create(item_type=item, **kwargs)

    instance = World()
    instance.project = project
    instance.region = region
    return instance


@pytest.fixture
def reader(django_user_model):
    user = django_user_model.objects.create_user("reader", password="test")
    user.user_permissions.add(
        Permission.objects.get(content_type__app_label="aasov", codename="view_project")
    )
    return user


@pytest.fixture
def editor(reader):
    reader.user_permissions.add(
        Permission.objects.get(content_type__app_label="aasov", codename="edit_plan")
    )
    return reader


@pytest.fixture(autouse=True)
def aa_main_characters():
    """AA wraps plugin URLs with main-character-required; use realistic local profiles."""
    from django.apps import apps

    if not apps.is_installed("allianceauth.authentication"):
        yield
        return
    from allianceauth.authentication.models import UserProfile
    from allianceauth.eveonline.models import EveCharacter
    from django.contrib.auth import get_user_model
    from django.db.models.signals import post_save

    def attach(sender, instance, created, **kwargs):
        if created:
            character = EveCharacter.objects.create(
                character_id=90000000 + instance.pk,
                character_name=instance.username,
                corporation_id=98000001,
                corporation_name="Test Corporation",
                corporation_ticker="TEST",
            )
            UserProfile.objects.filter(user=instance).update(main_character=character)

    post_save.connect(attach, sender=get_user_model(), weak=False)
    yield
    post_save.disconnect(attach, sender=get_user_model())
