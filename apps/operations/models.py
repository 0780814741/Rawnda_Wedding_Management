from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.core.models import TimeStampedModel
from apps.planning.models import Ceremony, WeddingProject
from apps.vendors.constants import PriceStatus
from apps.vendors.models import Vendor, VendorCategory, VendorPackage


class InvalidTransition(Exception):
    pass


class StatusMachineMixin:
    """Subclasses define TRANSITIONS = {status: {allowed next statuses}}."""

    TRANSITIONS: dict = {}

    def can_transition_to(self, new_status: str) -> bool:
        return new_status in self.TRANSITIONS.get(self.status, set())

    def _assert_transition(self, new_status: str):
        if not self.can_transition_to(new_status):
            raise InvalidTransition(f"{self.__class__.__name__}: cannot go from '{self.status}' to '{new_status}'.")


class CustomerRequest(TimeStampedModel):
    class Kind(models.TextChoices):
        FULL_PLAN = "full_plan", "Complete wedding plan"
        CONSULTATION = "consultation", "Human-assisted consultation"

    class Status(models.TextChoices):
        NEW = "new", "New"
        IN_REVIEW = "in_review", "In review"
        VENDOR_OUTREACH = "vendor_outreach", "Contacting vendors"
        QUOTATION_PREP = "quotation_prep", "Preparing quotation"
        OFFER_SENT = "offer_sent", "Offer sent to customer"
        ACCEPTED = "accepted", "Accepted"
        CLOSED = "closed", "Closed"
        CANCELLED = "cancelled", "Cancelled"

    project = models.ForeignKey(WeddingProject, on_delete=models.CASCADE, related_name="requests")
    kind = models.CharField(max_length=15, choices=Kind.choices, default=Kind.FULL_PLAN)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.NEW, db_index=True)
    message = models.TextField(blank=True, max_length=2000)
    contact_phone = models.CharField(max_length=30, blank=True)
    preferred_contact = models.CharField(max_length=10, choices=[("phone", "Phone call"), ("whatsapp", "WhatsApp"), ("email", "Email")], default="whatsapp")
    consent_vendor_outreach = models.BooleanField(
        default=False, help_text="Customer agreed that we contact vendors for them, sharing only date, place, guest count and needs.")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="assigned_requests")
    internal_notes = models.TextField(blank=True, help_text="PRIVATE. Never shown to customers.")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "assigned_to"])]

    def __str__(self):
        return f"Request #{self.pk} - {self.project.title}"


class VendorInquiry(TimeStampedModel):
    """Staff contact with a vendor (done outside the customer-facing site)."""

    class Status(models.TextChoices):
        TO_CONTACT = "to_contact", "To contact"
        AWAITING = "awaiting", "Awaiting reply"
        QUOTED = "quoted", "Vendor quoted"
        DECLINED = "declined", "Vendor declined / unavailable"
        NO_RESPONSE = "no_response", "No response"

    class Channel(models.TextChoices):
        PHONE = "phone", "Phone"
        WHATSAPP = "whatsapp", "WhatsApp"
        EMAIL = "email", "Email"
        IN_PERSON = "in_person", "In person"

    request = models.ForeignKey(CustomerRequest, on_delete=models.CASCADE, related_name="inquiries")
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT, related_name="inquiries")
    package = models.ForeignKey(VendorPackage, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    ceremony = models.ForeignKey(Ceremony, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.TO_CONTACT)
    channel = models.CharField(max_length=10, choices=Channel.choices, blank=True)
    contacted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    contacted_at = models.DateTimeField(null=True, blank=True)
    quoted_price_rwf = models.PositiveBigIntegerField(null=True, blank=True, help_text="Customer-facing price the vendor quoted.")
    quote_valid_until = models.DateField(null=True, blank=True)
    evidence_reference = models.CharField(max_length=255, blank=True, help_text="Where the vendor's reply is kept, e.g. WhatsApp thread 14 Oct.")
    internal_notes = models.TextField(blank=True, help_text="PRIVATE negotiation notes. Never shown to customers.")

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "vendor inquiries"

    def __str__(self):
        return f"{self.vendor} for request #{self.request_id}"

    def clean(self):
        if self.status == self.Status.QUOTED:
            if not self.quoted_price_rwf or not self.quote_valid_until or not self.evidence_reference.strip():
                raise ValidationError("A quoted inquiry needs the quoted price, its validity date and an evidence reference.")


class Quotation(StatusMachineMixin, TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        INTERNAL_REVIEW = "internal_review", "Internal review"
        SENT = "sent", "Sent to customer"
        ACCEPTED = "accepted", "Accepted by customer"
        DECLINED = "declined", "Declined by customer"
        EXPIRED = "expired", "Expired"
        SUPERSEDED = "superseded", "Replaced by a newer version"

    TRANSITIONS = {
        Status.DRAFT: {Status.INTERNAL_REVIEW, Status.SENT},
        Status.INTERNAL_REVIEW: {Status.DRAFT, Status.SENT},
        Status.SENT: {Status.ACCEPTED, Status.DECLINED, Status.EXPIRED, Status.SUPERSEDED},
        Status.ACCEPTED: set(), Status.DECLINED: set(), Status.EXPIRED: set(), Status.SUPERSEDED: set(),
    }

    project = models.ForeignKey(WeddingProject, on_delete=models.PROTECT, related_name="quotations")
    request = models.ForeignKey(CustomerRequest, null=True, blank=True, on_delete=models.SET_NULL, related_name="quotations")
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)
    valid_until = models.DateField(null=True, blank=True)
    # Totals are stored (integer RWF) and recalculated from the lines.
    subtotal_rwf = models.PositiveBigIntegerField(default=0)
    fee_bps = models.PositiveIntegerField(default=0)
    fee_rwf = models.PositiveBigIntegerField(default=0, help_text="Disclosed platform service fee.")
    contingency_rwf = models.PositiveBigIntegerField(default=0, help_text="Reserve for unexpected costs; NOT billed unless used.")
    payable_total_rwf = models.PositiveBigIntegerField(default=0, help_text="Subtotal + service fee: what the customer is asked to pay.")
    estimated_total_rwf = models.PositiveBigIntegerField(default=0, help_text="Payable total + contingency reserve.")
    notes_for_customer = models.TextField(blank=True, help_text="Shown to the customer.")
    internal_notes = models.TextField(blank=True, help_text="PRIVATE. Never shown to customers.")
    sent_at = models.DateTimeField(null=True, blank=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["project", "version"], name="unique_quotation_version")]
        permissions = [("send_quotation", "Can send quotations to customers")]

    def __str__(self):
        return f"Quotation v{self.version} - {self.project.title}"

    def save(self, *args, **kwargs):
        if not self.pk and not self.fee_bps:
            self.fee_bps = self.project.fee_bps
        super().save(*args, **kwargs)

    @property
    def fee_percent(self):
        return self.fee_bps / 100

    @property
    def is_expired(self):
        from django.utils import timezone
        return bool(self.valid_until and self.valid_until < timezone.localdate())

    def recalculate(self, save=True):
        from apps.core.money import bps_of
        subtotal = sum(l.line_total_rwf for l in self.lines.all())
        self.subtotal_rwf = subtotal
        self.fee_rwf = bps_of(subtotal, self.fee_bps)
        self.contingency_rwf = bps_of(subtotal, self.project.contingency_bps)
        self.payable_total_rwf = subtotal + self.fee_rwf
        self.estimated_total_rwf = self.payable_total_rwf + self.contingency_rwf
        if save:
            self.save(update_fields=["subtotal_rwf", "fee_rwf", "contingency_rwf", "payable_total_rwf", "estimated_total_rwf", "updated_at"])

    @property
    def remaining_budget_rwf(self):
        return (self.project.budget_rwf or 0) - self.estimated_total_rwf

    def has_unconfirmed_prices(self):
        return self.lines.filter(price_status=PriceStatus.INDICATIVE).exists()


class QuotationLine(TimeStampedModel):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")
    ceremony = models.ForeignKey(Ceremony, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    category = models.ForeignKey(VendorCategory, on_delete=models.PROTECT, related_name="+")
    vendor = models.ForeignKey(Vendor, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    package = models.ForeignKey(VendorPackage, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    vendor_name = models.CharField(max_length=150)
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    unit_price_rwf = models.PositiveBigIntegerField()
    line_total_rwf = models.PositiveBigIntegerField(default=0, editable=False)
    price_status = models.CharField(max_length=15, choices=PriceStatus.choices, default=PriceStatus.INDICATIVE)
    private_vendor_cost_rwf = models.PositiveBigIntegerField(null=True, blank=True, help_text="PRIVATE supplier cost. Never shown to customers.")
    internal_notes = models.CharField(max_length=255, blank=True, help_text="PRIVATE.")

    class Meta:
        ordering = ["ceremony_id", "category__ordering", "id"]

    def __str__(self):
        return f"{self.description} ({self.vendor_name})"

    def save(self, *args, **kwargs):
        if self.quotation.status not in (Quotation.Status.DRAFT, Quotation.Status.INTERNAL_REVIEW):
            raise ValidationError("Lines can only change while the quotation is a draft or in internal review. Create a new version instead.")
        self.line_total_rwf = self.unit_price_rwf * self.quantity
        super().save(*args, **kwargs)
        self.quotation.recalculate()

    def delete(self, *args, **kwargs):
        if self.quotation.status not in (Quotation.Status.DRAFT, Quotation.Status.INTERNAL_REVIEW):
            raise ValidationError("Lines can only change while the quotation is a draft or in internal review.")
        q = self.quotation
        super().delete(*args, **kwargs)
        q.recalculate()


class Booking(StatusMachineMixin, TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending_vendor", "Awaiting vendor confirmation"
        CONFIRMED = "confirmed", "Confirmed by vendor"
        CANCELLED = "cancelled", "Cancelled"
        COMPLETED = "completed", "Completed"

    TRANSITIONS = {
        Status.PENDING: {Status.CONFIRMED, Status.CANCELLED},
        Status.CONFIRMED: {Status.COMPLETED, Status.CANCELLED},
        Status.COMPLETED: set(), Status.CANCELLED: set(),
    }

    project = models.ForeignKey(WeddingProject, on_delete=models.PROTECT, related_name="bookings")
    quotation_line = models.OneToOneField(QuotationLine, on_delete=models.PROTECT, related_name="booking")
    vendor = models.ForeignKey(Vendor, null=True, on_delete=models.SET_NULL, related_name="bookings")
    ceremony = models.ForeignKey(Ceremony, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    vendor_name = models.CharField(max_length=150)
    description = models.CharField(max_length=255)
    service_date = models.DateField(null=True, blank=True)
    # Price snapshot from the accepted quotation line: later package changes never alter it.
    agreed_price_rwf = models.PositiveBigIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    confirmation_evidence = models.TextField(blank=True, help_text="Required to confirm: how and when the vendor confirmed (e.g. WhatsApp 15 Oct 10:42).")
    confirmed_at = models.DateTimeField(null=True, blank=True)
    confirmed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    internal_notes = models.TextField(blank=True, help_text="PRIVATE.")

    class Meta:
        ordering = ["service_date", "id"]
        permissions = [("confirm_booking", "Can confirm bookings with evidence")]
        indexes = [models.Index(fields=["vendor", "service_date"])]

    def __str__(self):
        return f"Booking #{self.pk}: {self.description}"
