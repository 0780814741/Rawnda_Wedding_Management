"""State transitions and workflows. Views and admin actions call these; nothing edits status directly."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from apps.core.audit import log_action
from apps.core.models import Notification
from apps.planning.models import WeddingProject

from .models import Booking, CustomerRequest, InvalidTransition, Quotation, QuotationLine


def create_request(project, customer, *, kind, message, phone, preferred_contact, consent):
    if not consent:
        raise ValidationError("Consent is required so we can contact vendors on your behalf.")
    if not project.plan_items.exists() and kind == CustomerRequest.Kind.FULL_PLAN:
        raise ValidationError("Please apply a plan option first, or choose a consultation.")
    with transaction.atomic():
        req = CustomerRequest.objects.create(
            project=project, kind=kind, message=message, contact_phone=phone,
            preferred_contact=preferred_contact, consent_vendor_outreach=True,
        )
        project.status = WeddingProject.Status.REQUESTED
        project.save(update_fields=["status", "updated_at"])
        log_action(customer, "request_submitted", req)
    return req


@transaction.atomic
def build_draft_quotation(project, request=None, actor=None):
    """Copy the working plan into a NEW draft quotation (snapshot prices), next version number."""
    last = project.quotations.order_by("-version").first()
    q = Quotation.objects.create(
        project=project, request=request, version=(last.version + 1) if last else 1,
        fee_bps=project.fee_bps, created_by=actor,
    )
    for item in project.plan_items.select_related("category"):
        QuotationLine.objects.create(
            quotation=q, ceremony=item.ceremony, category=item.category, vendor=item.vendor, package=item.package,
            vendor_name=item.vendor_name, description=f"{item.category.name}: {item.package_name}",
            quantity=item.quantity, unit_price_rwf=item.unit_price_rwf, price_status=item.price_status,
        )
    q.recalculate()
    log_action(actor, "quotation_drafted", q)
    return q


def move_quotation_to_review(q, actor=None):
    q._assert_transition(Quotation.Status.INTERNAL_REVIEW)
    q.status = Quotation.Status.INTERNAL_REVIEW
    q.save(update_fields=["status", "updated_at"])
    log_action(actor, "quotation_to_review", q)


@transaction.atomic
def send_quotation(q, actor):
    """Staff action: needs lines, a future validity date, and the send_quotation permission."""
    if actor is None or not actor.has_perm("operations.send_quotation"):
        raise PermissionError("You do not have permission to send quotations.")
    q._assert_transition(Quotation.Status.SENT)
    if not q.lines.exists():
        raise ValidationError("Add at least one line before sending.")
    if not q.valid_until or q.valid_until <= timezone.localdate():
        raise ValidationError("Set a validity date in the future before sending.")
    q.recalculate()
    for older in q.project.quotations.filter(status=Quotation.Status.SENT).exclude(pk=q.pk):
        older.status = Quotation.Status.SUPERSEDED
        older.save(update_fields=["status", "updated_at"])
    q.status, q.sent_at = Quotation.Status.SENT, timezone.now()
    q.save(update_fields=["status", "sent_at", "updated_at"])
    project = q.project
    project.status = WeddingProject.Status.OFFER_SENT
    project.save(update_fields=["status", "updated_at"])
    CustomerRequest.objects.filter(pk=q.request_id).update(status=CustomerRequest.Status.OFFER_SENT)
    Notification.objects.create(
        user=project.customer, kind=Notification.Kind.QUOTATION, title="Your wedding offer is ready",
        body="Please review the offer and the prices marked as confirmed or indicative.", url=f"/plan/{project.pk}/dashboard/",
    )
    log_action(actor, "quotation_sent", q, changes={"payable_total_rwf": q.payable_total_rwf})
    return q


def accept_quotation(q, customer):
    """Customer accepts: creates bookings in 'awaiting vendor confirmation' (NOT confirmed)."""
    if q.project.customer_id != customer.pk:
        raise PermissionError("Not your quotation.")
    if q.status != Quotation.Status.SENT:
        raise InvalidTransition("This offer can no longer be accepted.")
    if q.is_expired:
        # Persist the expiry first (outside the atomic block below) so the rollback of a failed accept can't undo it.
        q.status = Quotation.Status.EXPIRED
        q.save(update_fields=["status", "updated_at"])
        raise InvalidTransition("This offer has expired. Please ask us for an updated one.")
    return _accept(q, customer)


@transaction.atomic
def _accept(q, customer):
    from apps.payments.services import create_financial_records_for_booking

    q = Quotation.objects.select_for_update().get(pk=q.pk)
    q._assert_transition(Quotation.Status.ACCEPTED)  # guards a double-click / concurrent accept
    q.status, q.responded_at = Quotation.Status.ACCEPTED, timezone.now()
    q.save(update_fields=["status", "responded_at", "updated_at"])
    for line in q.lines.select_related("ceremony"):
        booking = Booking.objects.create(
            project=q.project, quotation_line=line, vendor=line.vendor, ceremony=line.ceremony,
            vendor_name=line.vendor_name, description=line.description,
            service_date=line.ceremony.effective_date if line.ceremony else q.project.wedding_date,
            agreed_price_rwf=line.line_total_rwf,
        )
        create_financial_records_for_booking(booking)
    q.project.status = WeddingProject.Status.ACCEPTED
    q.project.save(update_fields=["status", "updated_at"])
    CustomerRequest.objects.filter(pk=q.request_id).update(status=CustomerRequest.Status.ACCEPTED)
    log_action(customer, "quotation_accepted", q)
    return q


@transaction.atomic
def decline_quotation(q, customer):
    if q.project.customer_id != customer.pk:
        raise PermissionError("Not your quotation.")
    q._assert_transition(Quotation.Status.DECLINED)
    q.status, q.responded_at = Quotation.Status.DECLINED, timezone.now()
    q.save(update_fields=["status", "responded_at", "updated_at"])
    q.project.status = WeddingProject.Status.IN_PROGRESS
    q.project.save(update_fields=["status", "updated_at"])
    log_action(customer, "quotation_declined", q)


def expire_quotations():
    qs = Quotation.objects.filter(status=Quotation.Status.SENT, valid_until__lt=timezone.localdate())
    n = 0
    for q in qs:
        q.status = Quotation.Status.EXPIRED
        q.save(update_fields=["status", "updated_at"])
        n += 1
    return n


@transaction.atomic
def confirm_booking(booking, actor, evidence):
    if actor is None or not actor.has_perm("operations.confirm_booking"):
        raise PermissionError("You do not have permission to confirm bookings.")
    if not (evidence or "").strip():
        raise ValidationError("Evidence of the vendor's confirmation is required.")
    booking._assert_transition(Booking.Status.CONFIRMED)
    booking.status, booking.confirmation_evidence = Booking.Status.CONFIRMED, evidence.strip()
    booking.confirmed_at, booking.confirmed_by = timezone.now(), actor
    booking.save()
    Notification.objects.create(
        user=booking.project.customer, kind=Notification.Kind.BOOKING, title="A booking was confirmed by the vendor",
        body=booking.description, url=f"/plan/{booking.project_id}/dashboard/",
    )
    from apps.payments.services import refresh_settlements
    refresh_settlements(booking.project)
    log_action(actor, "booking_confirmed", booking)


def change_booking_status(booking, new_status, actor):
    booking._assert_transition(new_status)
    booking.status = new_status
    booking.save(update_fields=["status", "updated_at"])
    log_action(actor, f"booking_{new_status}", booking)
