from django.urls import path

from . import views

app_name = "aasov"
urlpatterns = [
    path("<int:project_id>/csv/", views.csv_upload, name="csv_upload"),
    path("<int:project_id>/csv/template/", views.csv_template, name="csv_template"),
    path(
        "<int:project_id>/systems/<int:system_id>/upgrades/<int:upgrade_id>/installed/",
        views.upgrade_installed,
        name="upgrade_installed",
    ),
    path(
        "<int:project_id>/constellations/<int:constellation_id>/ratting/",
        views.best_ratting,
        name="best_ratting",
    ),
    path(
        "<int:project_id>/systems/<int:system_id>/remove/",
        views.system_remove,
        name="system_remove",
    ),
    path("", views.index, name="index"),
    path("<int:project_id>/", views.project, name="project"),
    path("<int:project_id>/systems/<int:system_id>/mode/", views.system_mode, name="mode"),
    path(
        "<int:project_id>/systems/<int:system_id>/upgrades/add/", views.upgrade, name="upgrade_add"
    ),
    path(
        "<int:project_id>/systems/<int:system_id>/upgrades/<int:upgrade_id>/",
        views.upgrade,
        name="upgrade_edit",
    ),
    path("<int:project_id>/routes/preview/", views.route_preview, name="route_preview"),
    path("<int:project_id>/routes/add/", views.route, name="route_add"),
    path("<int:project_id>/routes/<int:route_id>/", views.route, name="route_edit"),
    path("<int:project_id>/remove/<str:kind>/<int:item_id>/", views.remove, name="remove"),
]
