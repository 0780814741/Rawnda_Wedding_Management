from django.db import connection
from django.http import JsonResponse
from django.shortcuts import render

from apps.vendors.models import Vendor, VendorCategory


def healthz(request):
    """Render health check: confirms the app can reach the database. Reveals no details."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)


def home(request):
    categories = VendorCategory.objects.filter(is_active=True)
    return render(request, "core/home.html", {"categories": categories, "has_vendors": Vendor.objects.public().exists()})


def how_it_works(request):
    return render(request, "core/how_it_works.html")


def pricing_and_fees(request):
    return render(request, "core/pricing.html")


def privacy(request):
    return render(request, "core/privacy.html")


def error_404(request, exception=None):
    return render(request, "404.html", status=404)


def error_500(request):
    return render(request, "500.html", status=500)
