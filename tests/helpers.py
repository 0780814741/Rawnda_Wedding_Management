from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.accounts.models import CustomerProfile
from apps.planning.models import Ceremony, WeddingProject
from apps.vendors.models import Vendor, VendorCategory, VendorPackage, VendorServiceArea

User = get_user_model()
PASSWORD = "S0me-long-test-pass!"


def make_customer(email="alice@example.com", **kw):
    u = User.objects.create_user(email=email, password=PASSWORD, first_name=kw.pop("first_name", "Alice"), **kw)
    CustomerProfile.objects.create(user=u, phone="0788000000")
    return u


def make_staff(email="ops@example.com", groups=("Operations Manager",)):
    u = User.objects.filter(email=email).first() or User.objects.create_user(email=email, password=PASSWORD, is_staff=True, role="staff")
    for g in groups:
        u.groups.add(Group.objects.get(name=g))
    return User.objects.get(pk=u.pk)  # fresh instance: no stale permission cache


def make_vendor(name="Vendor One", cats=("catering",), districts=("gasabo",), approved=True, **kw):
    v = Vendor.objects.create(
        name=name, status=Vendor.Status.APPROVED if approved else Vendor.Status.DRAFT,
        contact_phone=kw.pop("contact_phone", "0788123456"), contact_email=kw.pop("contact_email", "secret-vendor@example.com"),
        internal_notes=kw.pop("internal_notes", "SECRET NEGOTIATION NOTE"), **kw)
    v.categories.set(VendorCategory.objects.filter(slug__in=cats))
    for d in districts:
        VendorServiceArea.objects.create(vendor=v, district=d)
    return v


def make_package(vendor, category="catering", price=1_000_000, unit="flat", name="Pkg", min_guests=0, max_guests=None, ceremonies=None, **kw):
    return VendorPackage.objects.create(
        vendor=vendor, category=VendorCategory.objects.get(slug=category), name=name, price_rwf=price, pricing_unit=unit,
        min_guests=min_guests, max_guests=max_guests, ceremony_types=ceremonies or [],
        last_price_verified_at=timezone.localdate(), quote_valid_until=timezone.localdate() + timedelta(days=30), **kw)


def make_project(customer, ceremonies=("reception",), budget=10_000_000, guests=100, district="gasabo", **kw):
    p = WeddingProject.objects.create(
        customer=customer, title="Test wedding", wedding_date=timezone.localdate() + timedelta(days=120),
        district=district, guest_count=guests, budget_rwf=budget, **kw)
    for c in ceremonies:
        Ceremony.objects.create(project=p, ceremony_type=c)
    return p
