from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from apps.core.models import TimeStampedModel
from apps.operations.models import Booking, Quotation, StatusMachineMixin
from apps.planning.models import WeddingProject


class Invoice(StatusMachineMixin, TimeStampedModel):
    """Money the customer is asked to pay the platform. Paid status derives ONLY from verified payment records."""

    class Kind(models.TextChoices):
        FULL = "full", "Full payment"
        DEPOSIT = "deposit", "Deposit"
        BALANCE = "balance", "Balance"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ISSUED = "issued", "Issued - awaiting payment"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        PAID = "paid", "Paid"
        VOID = "void", "Void"

    TRANSITIONS = {
        Status.DRAFT: {Status.ISSUED, Status.VOID},
        Status.ISSUED: {Status.PARTIALLY_PAID, Status.PAID, Status.VOID},
        Status.PARTIALLY_PAID: {Status.PAID, Status.ISSUED, Status.VOID},
        Status.PAID: {Status.PARTIALLY_PAID, Status.ISSUED},  # only via payment reversal
        Status.VOID: set(),
    }

    number = models.CharField(max_length=30, unique=True)
    project = models.ForeignKey(WeddingProject, on_delete=models.PROTECT, related_name="invoices")
    quotation = models.ForeignKey(Quotation, on_delete=models.PROTECT, related_name="invoices")
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.FULL)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.DRAFT, db_index=True)
    currency = models.CharField(max_length=3, default="RWF", editable=False)
    amount_rwf = models.PositiveBigIntegerField()
    due_date = models.DateField(null=True, blank=True)
    payment_instructions = models.TextField(blank=True, help_text="Shown to the customer. Only official payment details.")
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    internal_notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(condition=Q(amount_rwf__gt=0), name="invoice_amount_positive")]

    def __str__(self):
        return self.number

    @property
    def paid_rwf(self):
        return sum(p.amount_rwf for p in self.payments.filter(status=PaymentRecord.Status.VERIFIED))

    @property
    def outstanding_rwf(self):
        return max(self.amount_rwf - self.paid_rwf, 0)


class PaymentRecord(TimeStampedModel):
    """One payment event. 'verified' requires a staff reconciliation (or, later, a verified provider event)."""

    class Method(models.TextChoices):
        BANK_TRANSFER = "bank_transfer", "Bank transfer"
        MOBILE_MONEY = "mobile_money", "Mobile money"
        CASH = "cash", "Cash at office"
        PROVIDER = "provider", "Payment provider (future)"

    class Status(models.TextChoices):
        PENDING = "pending", "Reported, awaiting verification"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected / not received"
        REVERSED = "reversed", "Reversed"

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    amount_rwf = models.PositiveBigIntegerField()
    method = models.CharField(max_length=15, choices=Method.choices, default=Method.BANK_TRANSFER)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING, db_index=True)
    reference = models.CharField(max_length=100, blank=True, help_text="Bank/mobile-money transaction reference.")
    provider = models.CharField(max_length=40, blank=True)
    external_id = models.CharField(max_length=120, blank=True, help_text="Provider payment ID (for idempotency).")
    received_at = models.DateTimeField(null=True, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    verified_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    verified_at = models.DateTimeField(null=True, blank=True)
    evidence_note = models.CharField(max_length=255, blank=True, help_text="How this was checked, e.g. statement line on 15 Oct.")

    class Meta:
        ordering = ["-created_at"]
        permissions = [("verify_payment", "Can verify or reject payments")]
        constraints = [
            models.CheckConstraint(condition=Q(amount_rwf__gt=0), name="payment_amount_positive"),
            models.UniqueConstraint(fields=["provider", "external_id"], condition=~Q(external_id=""), name="unique_provider_payment_id"),
        ]

    def __str__(self):
        return f"{self.invoice.number}: RWF {self.amount_rwf:,} ({self.get_status_display()})"


class ProviderEvent(models.Model):
    """Idempotency ledger for future payment-provider notifications."""

    provider = models.CharField(max_length=40)
    event_id = models.CharField(max_length=120)
    received_at = models.DateTimeField(auto_now_add=True)
    signature_valid = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["provider", "event_id"], name="unique_provider_event")]


class RefundRecord(TimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        PROCESSED = "processed", "Refund sent"
        REJECTED = "rejected", "Rejected"

    payment = models.ForeignKey(PaymentRecord, on_delete=models.PROTECT, related_name="refunds")
    amount_rwf = models.PositiveBigIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REQUESTED)
    reason = models.CharField(max_length=255)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    processed_at = models.DateTimeField(null=True, blank=True)
    reference = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [("approve_refund", "Can approve refunds")]
        constraints = [models.CheckConstraint(condition=Q(amount_rwf__gt=0), name="refund_amount_positive")]

    def clean(self):
        if self.payment_id and self.amount_rwf:
            already = sum(r.amount_rwf for r in self.payment.refunds.exclude(pk=self.pk).exclude(status=self.Status.REJECTED))
            if already + self.amount_rwf > self.payment.amount_rwf:
                raise ValidationError("Refunds cannot exceed the amount paid.")

    def __str__(self):
        return f"Refund RWF {self.amount_rwf:,} ({self.get_status_display()})"


class CommissionRecord(TimeStampedModel):
    """Platform commission on one booking, kept separate from the customer total and vendor payable."""

    class Status(models.TextChoices):
        ACCRUED = "accrued", "Accrued (booking not yet confirmed/paid)"
        EARNED = "earned", "Earned"
        WAIVED = "waived", "Waived"
        REVERSED = "reversed", "Reversed"

    booking = models.OneToOneField(Booking, on_delete=models.PROTECT, related_name="commission")
    rate_bps = models.PositiveIntegerField()
    base_amount_rwf = models.PositiveBigIntegerField()
    commission_rwf = models.PositiveBigIntegerField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACCRUED)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Commission RWF {self.commission_rwf:,} on booking {self.booking_id}"


class VendorSettlementRecord(TimeStampedModel):
    """What we owe the vendor. We do NOT move vendor funds automatically; staff record settlement manually."""

    class Status(models.TextChoices):
        NOT_DUE = "not_due", "Not due"
        DUE = "due", "Due"
        SCHEDULED = "scheduled", "Scheduled"
        PAID = "paid", "Paid to vendor"
        DISPUTED = "disputed", "Disputed"
        CANCELLED = "cancelled", "Cancelled"

    booking = models.OneToOneField(Booking, on_delete=models.PROTECT, related_name="settlement")
    gross_amount_rwf = models.PositiveBigIntegerField()
    commission_rwf = models.PositiveBigIntegerField(default=0)
    provider_fees_rwf = models.PositiveBigIntegerField(default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NOT_DUE)
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=100, blank=True)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        permissions = [("manage_settlement", "Can manage vendor settlements")]
        constraints = [models.CheckConstraint(condition=Q(commission_rwf__lte=models.F("gross_amount_rwf")), name="commission_within_gross")]

    @property
    def net_payable_rwf(self):
        return max(self.gross_amount_rwf - self.commission_rwf - self.provider_fees_rwf, 0)

    def __str__(self):
        return f"Settlement booking {self.booking_id}: RWF {self.net_payable_rwf:,}"
