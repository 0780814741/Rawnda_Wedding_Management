from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel
from apps.vendors.constants import CeremonyType, District, PriceStatus
from apps.vendors.models import Vendor, VendorCategory, VendorPackage


class WeddingProject(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft (planning)"
        REQUESTED = "requested", "Plan requested"
        IN_PROGRESS = "in_progress", "Our team is working on it"
        OFFER_SENT = "offer_sent", "Offer ready for you"
        ACCEPTED = "accepted", "Offer accepted"
        CANCELLED = "cancelled", "Cancelled"

    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wedding_projects")
    title = models.CharField(max_length=120, default="Our wedding")
    wedding_date = models.DateField(null=True, blank=True)
    district = models.CharField(max_length=20, choices=District.choices, blank=True)
    guest_count = models.PositiveIntegerField(validators=[MinValueValidator(1)], null=True, blank=True,
                                              help_text="A number only. We never ask for guest names.")
    budget_rwf = models.PositiveBigIntegerField(validators=[MinValueValidator(1)], null=True, blank=True)
    priorities = models.JSONField(default=list, blank=True, help_text="Category slugs the couple cares most about.")
    preferences = models.TextField(blank=True, max_length=2000)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.DRAFT, db_index=True)
    setup_step = models.PositiveSmallIntegerField(default=1)
    # Rates are snapshotted at creation so later configuration changes don't silently alter a customer's plan.
    fee_bps = models.PositiveIntegerField(default=0)
    contingency_bps = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["customer", "status"])]
        constraints = [
            models.CheckConstraint(condition=Q(guest_count__isnull=True) | Q(guest_count__gte=1), name="project_guests_positive"),
            models.CheckConstraint(condition=Q(budget_rwf__isnull=True) | Q(budget_rwf__gte=1), name="project_budget_positive"),
        ]

    def __str__(self):
        return f"{self.title} ({self.customer})"

    def save(self, *args, **kwargs):
        if not self.pk:
            self.fee_bps = settings.PLATFORM_SERVICE_FEE_BPS
            self.contingency_bps = settings.CONTINGENCY_BPS
        super().save(*args, **kwargs)

    @property
    def basics_complete(self):
        return all([self.wedding_date, self.district, self.guest_count, self.budget_rwf])

    @property
    def is_locked(self):
        """After a request is submitted, the customer can no longer silently change the plan."""
        return self.status != self.Status.DRAFT


class Ceremony(TimeStampedModel):
    project = models.ForeignKey(WeddingProject, on_delete=models.CASCADE, related_name="ceremonies")
    ceremony_type = models.CharField(max_length=15, choices=CeremonyType.choices)
    event_date = models.DateField(null=True, blank=True, help_text="Leave empty to use the wedding date.")
    guest_count = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)],
                                              help_text="Leave empty to use the overall guest count.")
    district = models.CharField(max_length=20, choices=District.choices, blank=True, help_text="Leave empty to use the wedding district.")
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["id"]
        verbose_name_plural = "ceremonies"
        constraints = [models.UniqueConstraint(fields=["project", "ceremony_type"], name="one_ceremony_type_per_project")]

    def __str__(self):
        return f"{self.get_ceremony_type_display()} - {self.project}"

    @property
    def effective_guests(self):
        return self.guest_count or self.project.guest_count or 0

    @property
    def effective_district(self):
        return self.district or self.project.district

    @property
    def effective_date(self):
        return self.event_date or self.project.wedding_date


class RequiredService(models.Model):
    """What the rules say each ceremony needs; regenerated whenever the setup changes."""

    ceremony = models.ForeignKey(Ceremony, on_delete=models.CASCADE, related_name="required_services")
    category = models.ForeignKey(VendorCategory, on_delete=models.PROTECT, related_name="+")
    is_essential = models.BooleanField(default=True)
    is_priority = models.BooleanField(default=False)
    allocated_rwf = models.PositiveBigIntegerField(default=0, help_text="Heuristic budget share, not a price.")

    class Meta:
        ordering = ["ceremony_id", "-is_essential", "category__ordering"]
        constraints = [models.UniqueConstraint(fields=["ceremony", "category"], name="one_required_service_per_category")]

    def __str__(self):
        return f"{self.ceremony}: {self.category}"


class PlanItem(TimeStampedModel):
    """A line of the working plan. Name and price are SNAPSHOTS so later package edits never change it."""

    class Source(models.TextChoices):
        AUTO = "auto", "Suggested by the planner"
        STAFF = "staff", "Set by our planning team"

    project = models.ForeignKey(WeddingProject, on_delete=models.CASCADE, related_name="plan_items")
    ceremony = models.ForeignKey(Ceremony, on_delete=models.CASCADE, related_name="plan_items")
    category = models.ForeignKey(VendorCategory, on_delete=models.PROTECT, related_name="+")
    package = models.ForeignKey(VendorPackage, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    vendor = models.ForeignKey(Vendor, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    vendor_name = models.CharField(max_length=150)
    package_name = models.CharField(max_length=150)
    quantity = models.PositiveIntegerField(default=1)
    unit_price_rwf = models.PositiveBigIntegerField()
    line_total_rwf = models.PositiveBigIntegerField()
    price_status = models.CharField(max_length=15, choices=PriceStatus.choices, default=PriceStatus.INDICATIVE)
    price_may_be_outdated = models.BooleanField(default=False)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.AUTO)
    strategy = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["ceremony_id", "category__ordering"]
        constraints = [
            models.UniqueConstraint(fields=["ceremony", "category"], name="one_plan_item_per_ceremony_category"),
            models.CheckConstraint(condition=Q(line_total_rwf=models.F("unit_price_rwf") * models.F("quantity")), name="plan_item_total_matches"),
        ]

    def __str__(self):
        return f"{self.package_name} ({self.vendor_name})"

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.ceremony_id and self.ceremony.project_id != self.project_id:
            raise ValidationError("The ceremony must belong to the same project.")

    def save(self, *args, **kwargs):
        self.line_total_rwf = self.unit_price_rwf * self.quantity
        super().save(*args, **kwargs)
