from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify

from apps.core.models import TimeStampedModel

from .constants import CeremonyType, District, PriceStatus
from .validators import validate_portfolio_image


class VendorCategory(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=80, unique=True)
    description = models.CharField(max_length=255, blank=True)
    ordering = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["ordering", "name"]
        verbose_name_plural = "vendor categories"

    def __str__(self):
        return self.name


class VendorQuerySet(models.QuerySet):
    def public(self):
        qs = self.filter(status=Vendor.Status.APPROVED)
        if not settings.SHOW_DEMO_VENDORS:
            qs = qs.filter(is_demo=False)
        return qs


class Vendor(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING = "pending", "Pending approval"
        APPROVED = "approved", "Approved"
        SUSPENDED = "suspended", "Suspended"
        ARCHIVED = "archived", "Archived"

    name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170, unique=True, blank=True)
    description = models.TextField(blank=True)
    categories = models.ManyToManyField(VendorCategory, related_name="vendors")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT, db_index=True)

    # Verification
    is_verified = models.BooleanField(default=False, help_text="Identity/business checked by our team.")
    verified_at = models.DateField(null=True, blank=True)
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    verification_notes = models.TextField(blank=True, help_text="PRIVATE. What was checked and how.")

    # PRIVATE contact details: never rendered on customer pages unless public_contact_approved.
    contact_name = models.CharField(max_length=120, blank=True)
    contact_phone = models.CharField(max_length=40, blank=True)
    contact_email = models.EmailField(blank=True)
    public_contact_approved = models.BooleanField(default=False, help_text="Only tick if the vendor agreed in writing to show contact details publicly.")
    internal_notes = models.TextField(blank=True, help_text="PRIVATE. Never shown to customers.")

    is_demo = models.BooleanField(default=False, help_text="Fictional demo data. Hidden in production unless SHOW_DEMO_VENDORS is on.")
    # Reserved for the later vendor-portal phase.
    portal_user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="vendor_profile")

    objects = VendorQuerySet.as_manager()

    class Meta:
        ordering = ["name"]
        permissions = [("approve_vendor", "Can approve, suspend and archive vendors"),
                       ("verify_vendor", "Can mark vendors as verified")]
        indexes = [models.Index(fields=["status", "is_demo"])]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:150] or "vendor"
            slug, n = base, 2
            while Vendor.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug, n = f"{base}-{n}", n + 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def is_public(self):
        return self.status == self.Status.APPROVED and (settings.SHOW_DEMO_VENDORS or not self.is_demo)


class VendorServiceArea(models.Model):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="service_areas")
    district = models.CharField(max_length=20, choices=District.choices)
    travel_note = models.CharField(max_length=200, blank=True, help_text="e.g. travel fee applies beyond Kigali.")

    class Meta:
        unique_together = [("vendor", "district")]
        indexes = [models.Index(fields=["district"])]

    def __str__(self):
        return f"{self.vendor} - {self.get_district_display()}"


class VendorPackage(TimeStampedModel):
    class Unit(models.TextChoices):
        FLAT = "flat", "Flat price"
        PER_GUEST = "per_guest", "Per guest"

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="packages")
    category = models.ForeignKey(VendorCategory, on_delete=models.PROTECT, related_name="packages")
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    ceremony_types = models.JSONField(default=list, blank=True, help_text="List of ceremony codes this package suits. Empty = any ceremony.")
    pricing_unit = models.CharField(max_length=12, choices=Unit.choices, default=Unit.FLAT)
    price_rwf = models.PositiveBigIntegerField(validators=[MinValueValidator(0)], help_text="Integer RWF (per guest if unit is per guest).")
    price_status = models.CharField(max_length=15, choices=PriceStatus.choices[:2], default=PriceStatus.INDICATIVE)
    last_price_verified_at = models.DateField(null=True, blank=True)
    quote_valid_until = models.DateField(null=True, blank=True)
    min_guests = models.PositiveIntegerField(default=0)
    max_guests = models.PositiveIntegerField(null=True, blank=True, help_text="Leave empty for no upper limit.")
    is_active = models.BooleanField(default=True)
    internal_notes = models.TextField(blank=True, help_text="PRIVATE. Supplier negotiation notes; never shown to customers.")
    private_supplier_price_rwf = models.PositiveBigIntegerField(null=True, blank=True, help_text="PRIVATE supplier cost. Never shown to customers.")

    class Meta:
        ordering = ["vendor__name", "category__ordering", "price_rwf"]
        indexes = [models.Index(fields=["category", "is_active"]), models.Index(fields=["price_rwf"])]
        constraints = [
            models.CheckConstraint(condition=Q(max_guests__isnull=True) | Q(max_guests__gte=models.F("min_guests")), name="package_guest_range_valid"),
        ]

    def __str__(self):
        return f"{self.vendor.name}: {self.name}"

    def clean(self):
        if self.vendor_id and self.category_id and not self.vendor.categories.filter(pk=self.category_id).exists():
            raise ValidationError({"category": "This category is not assigned to the vendor. Add it to the vendor first."})
        for code in self.ceremony_types or []:
            if code not in CeremonyType.values:
                raise ValidationError({"ceremony_types": f"Unknown ceremony code: {code}"})

    def save(self, *args, **kwargs):
        previous = None
        if self.pk:
            previous = VendorPackage.objects.filter(pk=self.pk).values("price_rwf", "price_status").first()
        super().save(*args, **kwargs)
        if previous is None or previous["price_rwf"] != self.price_rwf or previous["price_status"] != self.price_status:
            PriceRecord.objects.create(
                package=self, price_rwf=self.price_rwf, pricing_unit=self.pricing_unit,
                price_status=self.price_status, valid_until=self.quote_valid_until,
            )

    def price_for(self, guests: int) -> int:
        """Integer RWF cost for a given guest count."""
        return self.price_rwf * guests if self.pricing_unit == self.Unit.PER_GUEST else self.price_rwf

    def fits_guests(self, guests: int) -> bool:
        return guests >= self.min_guests and (self.max_guests is None or guests <= self.max_guests)

    def suits_ceremony(self, ceremony_type: str) -> bool:
        return not self.ceremony_types or ceremony_type in self.ceremony_types

    @property
    def is_price_stale(self) -> bool:
        if self.quote_valid_until and self.quote_valid_until < timezone.localdate():
            return True
        if not self.last_price_verified_at:
            return True
        return self.last_price_verified_at < timezone.localdate() - timedelta(days=settings.PRICE_STALE_DAYS)


class PackageInclusion(models.Model):
    package = models.ForeignKey(VendorPackage, on_delete=models.CASCADE, related_name="inclusions")
    description = models.CharField(max_length=200)
    is_included = models.BooleanField(default=True, help_text="Untick to list this as an exclusion.")
    ordering = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["ordering", "id"]

    def __str__(self):
        return self.description


class PackageAddOn(models.Model):
    package = models.ForeignKey(VendorPackage, on_delete=models.CASCADE, related_name="addons")
    name = models.CharField(max_length=150)
    price_rwf = models.PositiveBigIntegerField()
    note = models.CharField(max_length=200, blank=True)

    def __str__(self):
        return f"{self.name} (+RWF {self.price_rwf:,})"


class PriceRecord(models.Model):
    """History of package prices so past quotes/bookings never depend on today's package price."""

    class Source(models.TextChoices):
        ADMIN = "admin", "Entered by staff"
        RATE_CARD = "rate_card", "Vendor rate card"
        VENDOR_QUOTE = "vendor_quote", "Vendor quote"

    package = models.ForeignKey(VendorPackage, on_delete=models.CASCADE, related_name="price_history")
    price_rwf = models.PositiveBigIntegerField()
    pricing_unit = models.CharField(max_length=12, choices=VendorPackage.Unit.choices, default=VendorPackage.Unit.FLAT)
    price_status = models.CharField(max_length=15, choices=PriceStatus.choices[:2], default=PriceStatus.INDICATIVE)
    source = models.CharField(max_length=15, choices=Source.choices, default=Source.ADMIN)
    recorded_at = models.DateTimeField(auto_now_add=True)
    valid_until = models.DateField(null=True, blank=True)
    note = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-recorded_at"]

    def __str__(self):
        return f"{self.package_id}: RWF {self.price_rwf:,} @ {self.recorded_at:%Y-%m-%d}"


class AvailabilityCheck(TimeStampedModel):
    class Status(models.TextChoices):
        NOT_CHECKED = "not_checked", "Not checked"
        REQUESTED = "requested", "Asked vendor, awaiting reply"
        AVAILABLE = "available", "Vendor says available"
        UNAVAILABLE = "unavailable", "Vendor says unavailable"
        TENTATIVE = "tentative", "Tentative hold (not guaranteed)"

    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="availability_checks")
    package = models.ForeignKey(VendorPackage, null=True, blank=True, on_delete=models.SET_NULL, related_name="availability_checks")
    event_date = models.DateField()
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.NOT_CHECKED)
    checked_at = models.DateTimeField(null=True, blank=True)
    checked_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    evidence_note = models.CharField(max_length=255, blank=True, help_text="How this was confirmed, e.g. WhatsApp message on 12 Oct. Required for 'available'.")

    class Meta:
        ordering = ["-event_date"]
        indexes = [models.Index(fields=["vendor", "event_date"])]

    def __str__(self):
        return f"{self.vendor} {self.event_date} {self.get_status_display()}"

    def clean(self):
        if self.status == self.Status.AVAILABLE and not self.evidence_note.strip():
            raise ValidationError({"evidence_note": "Record how availability was confirmed before marking it available."})


class UploadedMedia(TimeStampedModel):
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name="media")
    image = models.ImageField(upload_to="vendor_portfolio/%Y/%m/", validators=[validate_portfolio_image])
    caption = models.CharField(max_length=200, blank=True)
    alt_text = models.CharField(max_length=200, blank=True, help_text="Short description for screen readers.")
    is_public = models.BooleanField(default=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "uploaded media"

    def __str__(self):
        return f"{self.vendor}: {self.caption or self.image.name}"

    def clean(self):
        if not self.pk and not settings.MEDIA_UPLOADS_ENABLED:
            raise ValidationError(
                "Image uploads are disabled because persistent storage is not configured. "
                "Set the AWS_STORAGE_BUCKET_NAME variables (see README) so photos survive redeploys."
            )
