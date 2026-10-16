from django.db.models import Min, Q
from django.shortcuts import get_object_or_404, render

from .constants import CeremonyType, District
from .models import Vendor, VendorCategory, VendorPackage


def _filtered_vendors(params):
    qs = Vendor.objects.public().prefetch_related("categories", "service_areas").distinct()
    category = params.get("category", "").strip()
    district = params.get("district", "").strip()
    q = params.get("q", "").strip()
    max_price = params.get("max_price", "").strip()
    guests = params.get("guests", "").strip()
    ceremony = params.get("ceremony", "").strip()

    pkg_filter = Q(packages__is_active=True)
    if category:
        qs = qs.filter(categories__slug=category)
        pkg_filter &= Q(packages__category__slug=category)
    if district in District.values:
        qs = qs.filter(service_areas__district=district)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(description__icontains=q))
    if ceremony in CeremonyType.values:
        # JSON containment is not portable to SQLite, so match in Python.
        ids = {p.vendor_id for p in VendorPackage.objects.filter(is_active=True).only("vendor_id", "ceremony_types") if p.suits_ceremony(ceremony)}
        qs = qs.filter(pk__in=ids)
    if guests.isdigit():
        n = int(guests)
        qs = qs.filter(Q(packages__max_guests__isnull=True) | Q(packages__max_guests__gte=n), packages__min_guests__lte=n, packages__is_active=True)
    if max_price.isdigit():
        qs = qs.filter(packages__price_rwf__lte=int(max_price), packages__is_active=True)
    return qs.annotate(from_price=Min("packages__price_rwf", filter=pkg_filter)).order_by("name")


def vendor_list(request):
    vendors = _filtered_vendors(request.GET)
    ctx = {
        "vendors": vendors[:60],
        "categories": VendorCategory.objects.filter(is_active=True),
        "districts": District.choices,
        "ceremonies": CeremonyType.choices,
        "params": request.GET,
    }
    template = "vendors/_results.html" if request.headers.get("HX-Request") else "vendors/list.html"
    return render(request, template, ctx)


def vendor_detail(request, slug):
    vendor = get_object_or_404(Vendor.objects.public().prefetch_related("categories", "service_areas"), slug=slug)
    packages = (
        VendorPackage.objects.filter(vendor=vendor, is_active=True)
        .select_related("category")
        .prefetch_related("inclusions", "addons")
    )
    media = vendor.media.filter(is_public=True)
    return render(request, "vendors/detail.html", {"vendor": vendor, "packages": packages, "media": media})
