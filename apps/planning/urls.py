from django.urls import path

from . import views

app_name = "planning"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("new/", views.new_project, name="new"),
    path("notifications/", views.notifications, name="notifications"),
    path("<int:pk>/setup/basics/", views.setup_basics, name="setup_basics"),
    path("<int:pk>/setup/ceremonies/", views.setup_ceremonies, name="setup_ceremonies"),
    path("<int:pk>/setup/preferences/", views.setup_preferences, name="setup_preferences"),
    path("<int:pk>/", views.plan, name="plan"),
    path("<int:pk>/apply/", views.apply_option, name="apply_option"),
    path("<int:pk>/request/", views.request_plan, name="request_plan"),
    path("<int:pk>/dashboard/", views.project_dashboard, name="project_dashboard"),
    path("<int:pk>/quotations/<int:qpk>/respond/", views.quotation_respond, name="quotation_respond"),
]
