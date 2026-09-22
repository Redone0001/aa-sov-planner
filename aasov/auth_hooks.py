from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook
from django.utils.translation import gettext_lazy as _

from . import urls


class SovPlannerMenu(MenuItemHook):
    def __init__(self):
        super().__init__(
            _("Sovereignty Planner"),
            "fa-solid fa-diagram-project",
            "aasov:index",
            navactive=["aasov:"],
        )

    def render(self, request):
        if request.user.has_perm("aasov.view_project"):
            return super().render(request)
        return ""


@hooks.register("menu_item_hook")
def register_menu():
    return SovPlannerMenu()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "aasov", r"^sov-planner/")
