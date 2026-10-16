"""Seed CLEARLY LABELLED FICTIONAL demo vendors for local development and demos only."""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.vendors.models import PackageInclusion, Vendor, VendorCategory, VendorPackage, VendorServiceArea

# (vendor, [categories], districts, [(category, package name, unit, price, min, max, ceremonies)])
DEMO = [
    ("[DEMO] Example Tents & Furniture", ["tent"], ["gasabo", "kicukiro", "nyarugenge"], [
        ("tent", "Standard tent set (demo)", "flat", 600_000, 0, 150, ["gusaba", "traditional", "reception"]),
        ("tent", "Large tent set (demo)", "flat", 1_400_000, 100, 400, ["gusaba", "traditional", "reception"])]),
    ("[DEMO] Sample Catering Co", ["catering"], ["gasabo", "kicukiro", "nyarugenge"], [
        ("catering", "Buffet basic (demo)", "per_guest", 8_000, 30, 400, []),
        ("catering", "Buffet premium (demo)", "per_guest", 14_000, 30, 400, [])]),
    ("[DEMO] Sample Venue Hall", ["venue"], ["gasabo", "kicukiro"], [
        ("venue", "Hall up to 200 guests (demo)", "flat", 1_500_000, 50, 200, ["reception"]),
        ("venue", "Hall up to 400 guests (demo)", "flat", 3_000_000, 150, 400, ["reception"])]),
    ("[DEMO] Example Decor Studio", ["decoration"], ["gasabo", "kicukiro", "nyarugenge"], [
        ("decoration", "Simple decor (demo)", "flat", 400_000, 0, None, []),
        ("decoration", "Full decor (demo)", "flat", 1_200_000, 0, None, [])]),
    ("[DEMO] Sample Photo & Video", ["photographer", "videographer"], ["gasabo", "kicukiro", "nyarugenge"], [
        ("photographer", "Photo half-day (demo)", "flat", 350_000, 0, None, []),
        ("photographer", "Photo full-day (demo)", "flat", 700_000, 0, None, []),
        ("videographer", "Video highlights (demo)", "flat", 500_000, 0, None, [])]),
    ("[DEMO] Example MC & Sound", ["mc", "sound-lighting", "dj"], ["gasabo", "kicukiro", "nyarugenge"], [
        ("mc", "MC for one event (demo)", "flat", 150_000, 0, None, []),
        ("sound-lighting", "Sound system (demo)", "flat", 300_000, 0, None, []),
        ("dj", "DJ evening (demo)", "flat", 250_000, 0, None, ["reception"])]),
    ("[DEMO] Sample Traditional Troupe", ["traditional-performers"], ["gasabo", "kicukiro", "nyarugenge"], [
        ("traditional-performers", "Dance and drum troupe (demo)", "flat", 300_000, 0, None, ["gusaba", "traditional"])]),
    ("[DEMO] Example Wedding Cars", ["transport"], ["gasabo", "kicukiro", "nyarugenge"], [
        ("transport", "Bridal car (demo)", "flat", 200_000, 0, None, [])]),
    ("[DEMO] Sample Bridal Wear", ["bridal-wear", "makeup"], ["gasabo", "kicukiro", "nyarugenge"], [
        ("bridal-wear", "Dress hire (demo)", "flat", 250_000, 0, None, []),
        ("makeup", "Bridal makeup (demo)", "flat", 120_000, 0, None, [])]),
    ("[DEMO] Example Cakes", ["cake"], ["gasabo", "kicukiro", "nyarugenge"], [
        ("cake", "Three-tier cake (demo)", "flat", 180_000, 0, None, ["reception"])]),
    ("[DEMO] Sample Choir", ["singer"], ["gasabo", "kicukiro", "nyarugenge"], [
        ("singer", "Church choir (demo)", "flat", 150_000, 0, None, ["church"])]),
]


class Command(BaseCommand):
    help = "Create fictional demo vendors (all marked is_demo=True, unverified). Development/demo only."

    def add_arguments(self, parser):
        parser.add_argument("--allow-production", action="store_true", help="Allow when DEBUG is off (staging demos only).")

    def handle(self, *args, **opts):
        if not settings.DEBUG and not opts["allow_production"]:
            raise CommandError("Refusing to seed demo data while DEBUG is off. Demo vendors are fictional; use --allow-production only for a staging demo.")
        today = timezone.localdate()
        created = 0
        for name, cats, districts, packages in DEMO:
            vendor, was_new = Vendor.objects.get_or_create(
                name=name, defaults={"description": "FICTIONAL demo vendor for development. Not a real business.",
                                     "status": Vendor.Status.APPROVED, "is_demo": True, "is_verified": False})
            vendor.categories.set(VendorCategory.objects.filter(slug__in=cats))
            for d in districts:
                VendorServiceArea.objects.get_or_create(vendor=vendor, district=d)
            for cat, pname, unit, price, mn, mx, cer in packages:
                pkg, pkg_new = VendorPackage.objects.get_or_create(
                    vendor=vendor, name=pname, defaults=dict(
                        category=VendorCategory.objects.get(slug=cat), pricing_unit=unit, price_rwf=price, min_guests=mn, max_guests=mx,
                        ceremony_types=cer, price_status="indicative", last_price_verified_at=today,
                        quote_valid_until=today + timedelta(days=60), description="Demo package with made-up pricing."))
                if pkg_new:
                    PackageInclusion.objects.create(package=pkg, description="Demo inclusion (fictional)", is_included=True)
                    created += 1
        self.stdout.write(self.style.SUCCESS(f"Demo data ready ({created} new packages). All records are fictional and flagged is_demo."))
