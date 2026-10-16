"""Payment workflow. A payment is real only when STAFF verify it (or a verified provider event, later)."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.core.audit import log_action
from apps.core.money import bps_of
from apps.core.models import Notification
from apps.operations.models import Booking, Quotation

from .models import CommissionRecord, Invoice, PaymentRecord, RefundRecord, VendorSettlementRecord


def create_financial_records_for_booking(booking):
    bps = settings.VENDOR_COMMISSION_BPS
    commission = bps_of(booking.agreed_price_rwf, bps)
    CommissionRecord.objects.create(booking=booking, rate_bps=bps, base_amount_rwf=booking.agreed_price_rwf, commission_rwf=commission)
    VendorSettlementRecord.objects.create(booking=booking, gross_amount_rwf=booking.agreed_price_rwf, commission_rwf=commission)


@transaction.atomic
def create_invoice(quotation, actor, *, amount_rwf=None, kind=Invoice.Kind.FULL, due_date=None, instructions=""):
    if quotation.status != Quotation.Status.ACCEPTED:
        raise ValidationError("Invoices can only be created from an accepted quotation.")
    already = quotation.invoices.exclude(status=Invoice.Status.VOID).aggregate(t=Sum("amount_rwf"))["t"] or 0
    amount = amount_rwf if amount_rwf is not None else quotation.payable_total_rwf - already
    if amount <= 0:
        raise ValidationError("Nothing left to invoice for this quotation.")
    if already + amount > quotation.payable_total_rwf:
        raise ValidationError("Invoices would exceed the quotation's payable total.")
    inv = Invoice.objects.create(
        number=f"TMP-{timezone.now().timestamp()}", project=quotation.project, quotation=quotation, kind=kind,
        amount_rwf=amount, due_date=due_date, payment_instructions=instructions or settings.PAYMENT_INSTRUCTIONS,
    )
    inv.number = f"INV-{timezone.localdate():%Y}-{inv.pk:06d}"
    inv.save(update_fields=["number"])
    log_action(actor, "invoice_created", inv, changes={"amount_rwf": amount})
    return inv


@transaction.atomic
def issue_invoice(invoice, actor):
    invoice._assert_transition(Invoice.Status.ISSUED)
    invoice.status, invoice.issued_at, invoice.issued_by = Invoice.Status.ISSUED, timezone.now(), actor
    invoice.save(update_fields=["status", "issued_at", "issued_by", "updated_at"])
    Notification.objects.create(
        user=invoice.project.customer, kind=Notification.Kind.PAYMENT, title=f"Invoice {invoice.number} is ready",
        body="See the payment instructions on your dashboard.", url=f"/plan/{invoice.project_id}/dashboard/",
    )
    log_action(actor, "invoice_issued", invoice)


def void_invoice(invoice, actor):
    if invoice.payments.filter(status=PaymentRecord.Status.VERIFIED).exists():
        raise ValidationError("An invoice with verified payments cannot be voided. Reverse the payments first.")
    invoice._assert_transition(Invoice.Status.VOID)
    invoice.status = Invoice.Status.VOID
    invoice.save(update_fields=["status", "updated_at"])
    log_action(actor, "invoice_voided", invoice)


def recalc_invoice_status(invoice):
    if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.VOID):
        return invoice
    paid = invoice.paid_rwf
    new = Invoice.Status.PAID if paid >= invoice.amount_rwf else Invoice.Status.PARTIALLY_PAID if paid > 0 else Invoice.Status.ISSUED
    if new != invoice.status:
        invoice.status = new
        invoice.save(update_fields=["status", "updated_at"])
    return invoice


def record_payment(invoice, actor, *, amount_rwf, method, reference="", evidence_note="", received_at=None):
    if invoice.status not in (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID):
        raise ValidationError("Payments can only be recorded against an issued invoice.")
    if amount_rwf <= 0:
        raise ValidationError("Amount must be greater than zero.")
    pay = PaymentRecord.objects.create(
        invoice=invoice, amount_rwf=amount_rwf, method=method, reference=reference, evidence_note=evidence_note,
        received_at=received_at, recorded_by=actor,
    )
    log_action(actor, "payment_recorded", pay, changes={"amount_rwf": amount_rwf})
    return pay


@transaction.atomic
def verify_payment(payment, actor):
    if actor is None or not actor.has_perm("payments.verify_payment"):
        raise PermissionError("You do not have permission to verify payments.")
    if payment.status != PaymentRecord.Status.PENDING:
        raise ValidationError("Only pending payments can be verified.")
    if not (payment.reference.strip() or payment.evidence_note.strip()):
        raise ValidationError("Add a transaction reference or evidence note before verifying.")
    invoice = Invoice.objects.select_for_update().get(pk=payment.invoice_id)
    if invoice.paid_rwf + payment.amount_rwf > invoice.amount_rwf:
        raise ValidationError("Verifying this payment would exceed the invoice amount.")
    payment.status, payment.verified_by, payment.verified_at = PaymentRecord.Status.VERIFIED, actor, timezone.now()
    payment.save(update_fields=["status", "verified_by", "verified_at", "updated_at"])
    recalc_invoice_status(invoice)
    refresh_settlements(invoice.project)
    Notification.objects.create(
        user=invoice.project.customer, kind=Notification.Kind.PAYMENT, title="We verified your payment",
        body=f"RWF {payment.amount_rwf:,} on invoice {invoice.number}.", url=f"/plan/{invoice.project_id}/dashboard/",
    )
    log_action(actor, "payment_verified", payment)


def reject_payment(payment, actor, reason=""):
    if actor is None or not actor.has_perm("payments.verify_payment"):
        raise PermissionError("You do not have permission to reject payments.")
    if payment.status != PaymentRecord.Status.PENDING:
        raise ValidationError("Only pending payments can be rejected.")
    payment.status = PaymentRecord.Status.REJECTED
    payment.evidence_note = (reason or payment.evidence_note)[:255]
    payment.save(update_fields=["status", "evidence_note", "updated_at"])
    log_action(actor, "payment_rejected", payment)


@transaction.atomic
def reverse_payment(payment, actor):
    if actor is None or not actor.has_perm("payments.verify_payment"):
        raise PermissionError("You do not have permission to reverse payments.")
    if payment.status != PaymentRecord.Status.VERIFIED:
        raise ValidationError("Only verified payments can be reversed.")
    payment.status = PaymentRecord.Status.REVERSED
    payment.save(update_fields=["status", "updated_at"])
    recalc_invoice_status(payment.invoice)
    refresh_settlements(payment.invoice.project)
    log_action(actor, "payment_reversed", payment)


def refresh_settlements(project):
    """A vendor settlement becomes 'due' only when the booking is vendor-confirmed AND the customer's
    verified payments cover the whole payable total of the accepted quotation. Never earlier."""
    q = project.quotations.filter(status=Quotation.Status.ACCEPTED).order_by("-version").first()
    if not q:
        return
    paid = sum(p.amount_rwf for inv in q.invoices.exclude(status=Invoice.Status.VOID)
               for p in inv.payments.filter(status=PaymentRecord.Status.VERIFIED))
    fully_paid = paid >= q.payable_total_rwf > 0
    for b in Booking.objects.filter(project=project).select_related("settlement", "commission"):
        s = b.settlement
        if s.status in (VendorSettlementRecord.Status.PAID, VendorSettlementRecord.Status.CANCELLED, VendorSettlementRecord.Status.DISPUTED, VendorSettlementRecord.Status.SCHEDULED):
            continue
        due = fully_paid and b.status in (Booking.Status.CONFIRMED, Booking.Status.COMPLETED)
        new = VendorSettlementRecord.Status.DUE if due else VendorSettlementRecord.Status.NOT_DUE
        if s.status != new:
            s.status = new
            s.save(update_fields=["status", "updated_at"])
        c = b.commission
        want = CommissionRecord.Status.EARNED if due else CommissionRecord.Status.ACCRUED
        if c.status in (CommissionRecord.Status.ACCRUED, CommissionRecord.Status.EARNED) and c.status != want:
            c.status = want
            c.save(update_fields=["status", "updated_at"])


@transaction.atomic
def approve_refund(refund, actor):
    if actor is None or not actor.has_perm("payments.approve_refund"):
        raise PermissionError("You do not have permission to approve refunds.")
    if refund.status != RefundRecord.Status.REQUESTED:
        raise ValidationError("Only requested refunds can be approved.")
    refund.full_clean()
    refund.status, refund.approved_by = RefundRecord.Status.APPROVED, actor
    refund.save(update_fields=["status", "approved_by", "updated_at"])
    log_action(actor, "refund_approved", refund)


def mark_refund_processed(refund, actor, reference):
    if actor is None or not actor.has_perm("payments.approve_refund"):
        raise PermissionError("You do not have permission to process refunds.")
    if refund.status != RefundRecord.Status.APPROVED:
        raise ValidationError("Refund must be approved first.")
    if not reference.strip():
        raise ValidationError("Enter the transfer reference of the refund you sent.")
    refund.status, refund.reference, refund.processed_at = RefundRecord.Status.PROCESSED, reference.strip(), timezone.now()
    refund.save(update_fields=["status", "reference", "processed_at", "updated_at"])
    log_action(actor, "refund_processed", refund)
