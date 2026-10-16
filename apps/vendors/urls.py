from django.urls import path

from . import views

app_name = "vendors"
urlpatterns = [
    path("", views.vendor_list, name="list"),
    path("<slug:slug>/", views.vendor_detail, name="detail"),
]
