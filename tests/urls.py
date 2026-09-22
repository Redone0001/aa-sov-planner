from django.contrib import admin
from django.urls import include, path

urlpatterns = [path("sov-planner/", include("aasov.urls")), path("admin/", admin.site.urls)]
