from django.urls import path

from . import views

app_name = "core"
urlpatterns = [
    path("", views.home, name="home"),
    path("how-it-works/", views.how_it_works, name="how_it_works"),
    path("pricing-and-fees/", views.pricing_and_fees, name="pricing"),
    path("privacy/", views.privacy, name="privacy"),
    path("healthz", views.healthz, name="healthz"),
]
