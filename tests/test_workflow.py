import hashlib
import hmac
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.operations import services as ops
from apps.operations.models import Booking, InvalidTransition, Quotation
from apps.payments import services as pay
from apps.payments.models import CommissionRecord, Invoice, PaymentRecord, ProviderEvent, RefundRecord, VendorSettlementRecord
from apps.payments.providers import ProviderNotConfigured, get_provider, record_provider_event, verify_hmac_sha256
from apps.planning import services as plan_services
from apps.planning.models import PlanItem

from .helpers import PASSWORD, make_customer, make_package, make_project, make_staff, make_vendor


def planned_project(customer=None, **kw):
    customer = customer or make_customer()
    v = make_vendor("Caterer", cats=("catering",)); pkg = make_package(v, "catering", 8_000, "per_guest", name="Buffet", min_guests=10)
    v2 = make_vendor("Venue", cats=("venue",)); pkg2 = make_package(v2, "venue", 1_500_000, name="Hall")
    p = make_project(customer, **kw)
    plan_services.apply_option(p, plan_services.generate_options(p)[0])
    return p, pkg, pkg2


def sent_quotation(project, staff=None):
    staff = staff or make_staff()
    q = ops.build_draft_quotation(project, actor=staff)
    q.valid_until = timezone.localdate() + timedelta(days=10); q.save()
    return ops.send_quotation(q, staff), staff


class PriceHandlingTests(TestCase):
    def test_price_history_recorded_on_change(self):
        pkg = make_package(make_vendor(), price=100_000)
        pkg.price_rwf = 120_000; pkg.save()
        self.assertEqual(list(pkg.price_history.order_by("id").values_list("price_rwf", flat=True)), [100_000, 120_000])

    def test_per_guest_and_flat_totals(self):
        v = make_vendor()
        self.assertEqual(make_package(v, unit="per_guest", price=7_500, name="a").price_for(120), 900_000)
        self.assertEqual(make_package(v, unit="flat", price=7_500, name="b").price_for(120), 7_500)

    def test_money_is_integer_fields(self):
        from django.db import models
        from apps.vendors.models import VendorPackage
        self.assertIsInstance(VendorPackage._meta.get_field("price_rwf"), models.PositiveBigIntegerField)

    def test_stale_price_detection(self):
        pkg = make_package(make_vendor())
        self.assertFalse(pkg.is_price_stale)
        pkg.last_price_verified_at = timezone.localdate() - timedelta(days=400); pkg.save()
        self.assertTrue(pkg.is_price_stale)
        pkg.last_price_verified_at = None; self.assertTrue(pkg.is_price_stale)

    def test_plan_item_and_quote_keep_historical_price(self):
        p, pkg, _ = planned_project()
        q, _ = sent_quotation(p)
        before = {l.pk: l.line_total_rwf for l in q.lines.all()}
        pkg.price_rwf = 99_999_999; pkg.save()
        self.assertEqual({l.pk: l.line_total_rwf for l in Quotation.objects.get(pk=q.pk).lines.all()}, before)
        item = p.plan_items.get(package=pkg)
        self.assertEqual(item.unit_price_rwf, 8_000)

    def test_missing_package_price_change_does_not_break_snapshot(self):
        p, pkg, _ = planned_project()
        pkg.delete()
        self.assertTrue(p.plan_items.filter(package__isnull=True, package_name="Buffet").exists())


class PlanTotalsTests(TestCase):
    def test_project_totals(self):
        p, *_ = planned_project(guests=100, budget=10_000_000)
        t = plan_services.project_totals(p)
        sub = 8_000 * 100 + 1_500_000
        self.assertEqual(t["subtotal"], sub)
        self.assertEqual(t["fee"], sub * 5 // 100)
        self.assertEqual(t["total"], t["subtotal"] + t["fee"] + t["contingency"])
        self.assertEqual(t["remaining"], 10_000_000 - t["total"])

    def test_apply_replaces_auto_items_and_keeps_staff_items(self):
        p, pkg, _ = planned_project()
        staff_item = p.plan_items.first(); staff_item.source = PlanItem.Source.STAFF; staff_item.save()
        n_before = p.plan_items.count()
        plan_services.apply_option(p, plan_services.generate_options(p)[0])
        self.assertEqual(p.plan_items.count(), n_before)
        self.assertTrue(p.plan_items.filter(pk=staff_item.pk).exists())


class QuotationWorkflowTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.project, *_ = planned_project(self.customer)
        self.staff = make_staff()

    def test_draft_copies_plan_and_totals(self):
        q = ops.build_draft_quotation(self.project, actor=self.staff)
        self.assertEqual(q.lines.count(), self.project.plan_items.count())
        self.assertEqual(q.subtotal_rwf, sum(i.line_total_rwf for i in self.project.plan_items.all()))
        self.assertEqual(q.payable_total_rwf, q.subtotal_rwf + q.fee_rwf)
        self.assertEqual(q.estimated_total_rwf, q.payable_total_rwf + q.contingency_rwf)

    def test_cannot_send_without_validity_or_permission(self):
        q = ops.build_draft_quotation(self.project)
        with self.assertRaises(ValidationError):
            ops.send_quotation(q, self.staff)
        q.valid_until = timezone.localdate() + timedelta(days=3); q.save()
        with self.assertRaises(PermissionError):
            ops.send_quotation(q, make_staff("planner@example.com", ("Planner",)))
        ops.send_quotation(q, self.staff)
        self.assertEqual(Quotation.objects.get(pk=q.pk).status, "sent")

    def test_invalid_transitions_blocked(self):
        q, _ = sent_quotation(self.project)
        with self.assertRaises(InvalidTransition):
            ops.move_quotation_to_review(q)
        with self.assertRaises(InvalidTransition):
            ops.send_quotation(q, self.staff)

    def test_lines_locked_after_send(self):
        q, _ = sent_quotation(self.project)
        line = q.lines.first(); line.unit_price_rwf = 1
        with self.assertRaises(ValidationError):
            line.save()
        with self.assertRaises(ValidationError):
            line.delete()

    def test_new_version_supersedes_old_sent_one(self):
        q1, staff = sent_quotation(self.project)
        q2 = ops.build_draft_quotation(self.project); q2.valid_until = timezone.localdate() + timedelta(days=5); q2.save()
        ops.send_quotation(q2, staff)
        self.assertEqual(Quotation.objects.get(pk=q1.pk).status, "superseded")
        self.assertEqual(q2.version, 2)

    def test_customer_accept_creates_unconfirmed_bookings_and_records(self):
        q, _ = sent_quotation(self.project)
        ops.accept_quotation(q, self.customer)
        bookings = Booking.objects.filter(project=self.project)
        self.assertEqual(bookings.count(), q.lines.count())
        self.assertTrue(all(b.status == Booking.Status.PENDING for b in bookings))
        self.assertTrue(all(b.confirmed_at is None for b in bookings))
        self.assertEqual(CommissionRecord.objects.count(), bookings.count())
        self.assertEqual(VendorSettlementRecord.objects.filter(status="not_due").count(), bookings.count())
        self.project.refresh_from_db(); self.assertEqual(self.project.status, "accepted")

    def test_cannot_accept_twice_expired_or_someone_elses(self):
        q, _ = sent_quotation(self.project)
        with self.assertRaises(PermissionError):
            ops.accept_quotation(q, make_customer("other@example.com"))
        Quotation.objects.filter(pk=q.pk).update(valid_until=timezone.localdate() - timedelta(days=1))
        with self.assertRaises(InvalidTransition):
            ops.accept_quotation(Quotation.objects.get(pk=q.pk), self.customer)
        self.assertEqual(Quotation.objects.get(pk=q.pk).status, "expired")

    def test_decline_then_cannot_accept(self):
        q, _ = sent_quotation(self.project)
        ops.decline_quotation(q, self.customer)
        with self.assertRaises(InvalidTransition):
            ops.accept_quotation(Quotation.objects.get(pk=q.pk), self.customer)

    def test_booking_confirmation_needs_evidence_and_permission(self):
        q, staff = sent_quotation(self.project); ops.accept_quotation(q, self.customer)
        b = Booking.objects.first()
        with self.assertRaises(ValidationError):
            ops.confirm_booking(b, staff, "   ")
        with self.assertRaises(PermissionError):
            ops.confirm_booking(b, make_staff("p2@example.com", ("Planner",)), "WhatsApp 15 Oct")
        ops.confirm_booking(b, staff, "Vendor confirmed on WhatsApp 15 Oct 10:42")
        b.refresh_from_db(); self.assertEqual(b.status, "confirmed"); self.assertIsNotNone(b.confirmed_at)
        with self.assertRaises(InvalidTransition):
            ops.confirm_booking(b, staff, "again")

    def test_expire_job(self):
        q, _ = sent_quotation(self.project)
        Quotation.objects.filter(pk=q.pk).update(valid_until=timezone.localdate() - timedelta(days=1))
        self.assertEqual(ops.expire_quotations(), 1)

    def test_request_requires_consent_and_plan(self):
        with self.assertRaises(ValidationError):
            ops.create_request(self.project, self.customer, kind="full_plan", message="", phone="1", preferred_contact="phone", consent=False)
        empty = make_project(self.customer)
        with self.assertRaises(ValidationError):
            ops.create_request(empty, self.customer, kind="full_plan", message="", phone="1", preferred_contact="phone", consent=True)


class PaymentTests(TestCase):
    def setUp(self):
        self.customer = make_customer()
        self.project, *_ = planned_project(self.customer)
        self.q, self.ops = sent_quotation(self.project)
        ops.accept_quotation(self.q, self.customer)
        self.finance = make_staff("fin@example.com", ("Finance",))
        self.inv = pay.create_invoice(Quotation.objects.get(pk=self.q.pk), self.finance)

    def test_invoice_only_from_accepted_and_not_over_total(self):
        other, _, _ = planned_project(make_customer("z@example.com"))
        q2, _ = sent_quotation(other)
        with self.assertRaises(ValidationError):
            pay.create_invoice(q2, self.finance)
        with self.assertRaises(ValidationError):
            pay.create_invoice(self.q, self.finance, amount_rwf=1)  # nothing left: full invoice already created
        self.assertEqual(self.inv.amount_rwf, self.q.payable_total_rwf)
        self.assertTrue(self.inv.number.startswith("INV-"))

    def test_payments_only_on_issued_invoice(self):
        with self.assertRaises(ValidationError):
            pay.record_payment(self.inv, self.finance, amount_rwf=1000, method="bank_transfer", reference="R1")
        pay.issue_invoice(self.inv, self.finance)
        p = pay.record_payment(self.inv, self.finance, amount_rwf=1000, method="bank_transfer", reference="R1")
        self.assertEqual(p.status, PaymentRecord.Status.PENDING)

    def test_pending_payment_is_not_paid(self):
        pay.issue_invoice(self.inv, self.finance)
        pay.record_payment(self.inv, self.finance, amount_rwf=self.inv.amount_rwf, method="mobile_money", reference="MM1")
        self.inv.refresh_from_db()
        self.assertEqual(self.inv.paid_rwf, 0); self.assertEqual(self.inv.status, Invoice.Status.ISSUED)

    def test_verify_needs_permission_evidence_and_blocks_overpayment(self):
        pay.issue_invoice(self.inv, self.finance)
        p = pay.record_payment(self.inv, self.finance, amount_rwf=1000, method="cash")
        with self.assertRaises(ValidationError):
            pay.verify_payment(p, self.finance)  # no reference/evidence
        p.reference = "RCPT-1"; p.save()
        with self.assertRaises(PermissionError):
            pay.verify_payment(p, make_staff("p3@example.com", ("Planner",)))
        big = pay.record_payment(self.inv, self.finance, amount_rwf=self.inv.amount_rwf, method="bank_transfer", reference="BIG")
        pay.verify_payment(p, self.finance)
        with self.assertRaises(ValidationError):
            pay.verify_payment(big, self.finance)  # would exceed invoice
        self.inv.refresh_from_db()
        self.assertEqual((self.inv.paid_rwf, self.inv.status), (1000, Invoice.Status.PARTIALLY_PAID))

    def test_full_payment_marks_paid_and_reversal_reopens(self):
        pay.issue_invoice(self.inv, self.finance)
        p = pay.record_payment(self.inv, self.finance, amount_rwf=self.inv.amount_rwf, method="bank_transfer", reference="FULL")
        pay.verify_payment(p, self.finance)
        self.inv.refresh_from_db(); self.assertEqual(self.inv.status, Invoice.Status.PAID)
        pay.reverse_payment(p, self.finance)
        self.inv.refresh_from_db(); self.assertEqual((self.inv.status, self.inv.paid_rwf), (Invoice.Status.ISSUED, 0))

    def test_cannot_void_invoice_with_verified_payment(self):
        pay.issue_invoice(self.inv, self.finance)
        p = pay.record_payment(self.inv, self.finance, amount_rwf=1000, method="cash", reference="C1")
        pay.verify_payment(p, self.finance)
        with self.assertRaises(ValidationError):
            pay.void_invoice(self.inv, self.finance)

    def test_settlement_due_only_when_confirmed_and_fully_paid(self):
        pay.issue_invoice(self.inv, self.finance)
        full = pay.record_payment(self.inv, self.finance, amount_rwf=self.inv.amount_rwf, method="bank_transfer", reference="F")
        pay.verify_payment(full, self.finance)
        self.assertFalse(VendorSettlementRecord.objects.filter(status="due").exists())  # paid but vendors not confirmed
        for b in Booking.objects.all():
            ops.confirm_booking(b, self.ops, "WhatsApp confirmation")
        self.assertFalse(VendorSettlementRecord.objects.exclude(status="due").exists())
        pay.reverse_payment(full, self.finance)
        self.assertFalse(VendorSettlementRecord.objects.filter(status="due").exists())

    def test_settlement_math_keeps_amounts_separate(self):
        s = VendorSettlementRecord.objects.first()
        s.commission_rwf, s.provider_fees_rwf = 1000, 200; s.save()
        self.assertEqual(s.net_payable_rwf, s.gross_amount_rwf - 1200)

    @override_settings(VENDOR_COMMISSION_BPS=1000)
    def test_commission_computed_on_accept(self):
        p2, *_ = planned_project(make_customer("c2@example.com"))
        q2, _ = sent_quotation(p2); ops.accept_quotation(q2, p2.customer)
        c = CommissionRecord.objects.filter(booking__project=p2).first()
        self.assertEqual(c.commission_rwf, c.base_amount_rwf // 10)

    def test_refund_cannot_exceed_payment(self):
        pay.issue_invoice(self.inv, self.finance)
        p = pay.record_payment(self.inv, self.finance, amount_rwf=5000, method="cash", reference="C")
        pay.verify_payment(p, self.finance)
        r = RefundRecord.objects.create(payment=p, amount_rwf=6000, reason="test")
        with self.assertRaises(ValidationError):
            pay.approve_refund(r, self.finance)
        r.amount_rwf = 4000; r.save(); pay.approve_refund(r, self.finance)
        with self.assertRaises(ValidationError):
            pay.mark_refund_processed(r, self.finance, "  ")
        pay.mark_refund_processed(r, self.finance, "TRF-1"); r.refresh_from_db()
        self.assertEqual(r.status, "processed")

    def test_customer_cannot_see_other_customers_invoice(self):
        pay.issue_invoice(self.inv, self.finance)
        make_customer("mallory@example.com")
        self.client.login(email="mallory@example.com", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("planning:project_dashboard", args=[self.project.pk])).status_code, 404)
        self.client.logout(); self.client.login(email=self.customer.email, password=PASSWORD)
        body = self.client.get(reverse("planning:project_dashboard", args=[self.project.pk])).content.decode()
        self.assertIn(self.inv.number, body); self.assertIn("How to pay", body); self.assertIn("only after our team verifies", body)


class ProviderTests(TestCase):
    def test_manual_is_default_and_has_no_notifications(self):
        prov = get_provider()
        self.assertEqual(prov.name, "manual")
        with self.assertRaises(ProviderNotConfigured):
            prov.verify_notification(b"{}", "sig")

    def test_webhook_disabled_by_default(self):
        r = self.client.post(reverse("payments:webhook", args=["any"]), data="{}", content_type="application/json")
        self.assertEqual(r.status_code, 501)
        self.assertEqual(self.client.get(reverse("payments:webhook", args=["any"])).status_code, 405)

    def test_hmac_verification(self):
        body = b'{"id":"1"}'; sig = hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_hmac_sha256("s3cret", body, sig))
        self.assertFalse(verify_hmac_sha256("s3cret", body + b" ", sig))
        self.assertFalse(verify_hmac_sha256("", body, sig)); self.assertFalse(verify_hmac_sha256("s3cret", body, ""))

    def test_event_idempotency(self):
        self.assertFalse(record_provider_event("p", "e1", signature_valid=False))
        self.assertTrue(record_provider_event("p", "e1", True))
        self.assertFalse(record_provider_event("p", "e1", True))
        self.assertEqual(ProviderEvent.objects.count(), 1)

    def test_browser_get_cannot_mark_anything_paid(self):
        # there is intentionally no customer-facing URL that changes payment state
        from django.urls import get_resolver
        names = {p.name for p in get_resolver().url_patterns[0].url_patterns if hasattr(p, "name")}
        self.assertFalse(any("paid" in (n or "") or "success" in (n or "") for n in names))


class JourneyTests(TestCase):
    """End to end through HTTP and services: signup -> setup -> plan -> request -> quote -> accept -> invoice."""

    def test_full_customer_journey(self):
        v = make_vendor("Hall", cats=("venue", "catering", "mc", "sound-lighting"))
        for cat, price, unit in (("venue", 1_200_000, "flat"), ("catering", 7_000, "per_guest"), ("mc", 100_000, "flat"), ("sound-lighting", 250_000, "flat")):
            make_package(v, cat, price, unit, name=cat)
        c = self.client
        r = c.post(reverse("accounts:signup"), {"first_name": "Jane", "email": "jane@example.com", "password1": PASSWORD, "password2": PASSWORD, "accept_terms": "on"})
        self.assertRedirects(r, reverse("planning:new"))
        date = (timezone.localdate() + timedelta(days=90)).isoformat()
        r = c.post(reverse("planning:new"), {"title": "Jane & John", "wedding_date": date, "district": "gasabo", "guest_count": 120, "budget_rwf": 8_000_000})
        from apps.planning.models import WeddingProject
        p = WeddingProject.objects.get(customer__email="jane@example.com")
        self.assertRedirects(r, reverse("planning:setup_ceremonies", args=[p.pk]))
        r = c.post(reverse("planning:setup_ceremonies", args=[p.pk]), {"ceremonies": ["reception"]})
        r = c.post(reverse("planning:setup_preferences", args=[p.pk]), {"priorities": ["catering"], "preferences": "Garden style"})
        self.assertRedirects(r, reverse("planning:plan", args=[p.pk]))
        page = c.get(reverse("planning:plan", args=[p.pk]))
        self.assertContains(page, "Budget-friendly"); self.assertContains(page, "indicative", status_code=200)
        r = c.post(reverse("planning:apply_option", args=[p.pk]), {"strategy": "budget"}); self.assertEqual(r.status_code, 302)
        page = c.get(reverse("planning:plan", args=[p.pk])); self.assertContains(page, "Estimated total"); self.assertContains(page, "Indicative price")
        r = c.post(reverse("planning:request_plan", args=[p.pk]), {"kind": "full_plan", "contact_phone": "0788", "preferred_contact": "whatsapp", "consent": "on"})
        self.assertRedirects(r, reverse("planning:project_dashboard", args=[p.pk]))
        p.refresh_from_db(); self.assertEqual(p.status, "requested")
        # locked afterwards
        self.assertEqual(c.post(reverse("planning:apply_option", args=[p.pk]), {"strategy": "budget"}).status_code, 302)
        # operator side
        staff = make_staff(groups=("Operations Manager", "Finance"))
        req = p.requests.get()
        q = ops.build_draft_quotation(p, req, staff); q.valid_until = timezone.localdate() + timedelta(days=7); q.notes_for_customer = "Prices checked"; q.save()
        ops.send_quotation(q, staff)
        page = c.get(reverse("planning:project_dashboard", args=[p.pk])); self.assertContains(page, "Accept offer"); self.assertContains(page, "Prices checked")
        c.post(reverse("planning:quotation_respond", args=[p.pk, q.pk]), {"action": "accept"})
        page = c.get(reverse("planning:project_dashboard", args=[p.pk]))
        self.assertContains(page, "Awaiting vendor confirmation"); self.assertNotContains(page, "Confirmed by vendor")
        inv = pay.create_invoice(Quotation.objects.get(pk=q.pk), staff); pay.issue_invoice(inv, staff)
        pay.record_payment(inv, staff, amount_rwf=inv.amount_rwf, method="bank_transfer", reference="BK-1")
        self.assertContains(c.get(reverse("planning:project_dashboard", args=[p.pk])), "Reported, awaiting verification")

    def test_setup_validation_messages(self):
        make_customer(); self.client.login(email="alice@example.com", password=PASSWORD)
        r = self.client.post(reverse("planning:new"), {"title": "x", "wedding_date": "2020-01-01", "district": "gasabo", "guest_count": 0, "budget_rwf": 5})
        self.assertContains(r, "future"); self.assertContains(r, "between 1 and 5,000"); self.assertContains(r, "very small")

    def test_empty_states(self):
        make_customer(); self.client.login(email="alice@example.com", password=PASSWORD)
        self.assertContains(self.client.get(reverse("planning:dashboard")), "have not started")
        self.assertContains(self.client.get(reverse("planning:notifications")), "No updates yet")


class HealthTests(TestCase):
    def test_health_ok_and_minimal(self):
        r = self.client.get("/healthz")
        self.assertEqual((r.status_code, r.json()), (200, {"status": "ok"}))

    def test_public_pages_render(self):
        for name in ("core:home", "core:how_it_works", "core:pricing", "core:privacy", "accounts:login", "accounts:signup", "vendors:list"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_404_is_generic(self):
        self.assertEqual(self.client.get("/nope/").status_code, 404)


class RetentionTests(TestCase):
    def test_purge_anonymises_requested_deletions_and_keeps_financial_rows(self):
        from django.core.management import call_command
        from apps.accounts.models import CustomerProfile
        c = make_customer("gone@example.com")
        p, *_ = planned_project(c)
        q, staff = sent_quotation(p); ops.accept_quotation(q, c)
        CustomerProfile.objects.filter(user=c).update(deletion_requested_at=timezone.now())
        call_command("purge_expired_data", "--dry-run"); c.refresh_from_db(); self.assertEqual(c.email, "gone@example.com")
        call_command("purge_expired_data"); c.refresh_from_db()
        self.assertTrue(c.email.startswith("deleted-")); self.assertFalse(c.is_active); self.assertFalse(c.has_usable_password())
        self.assertEqual(c.profile.phone, ""); self.assertTrue(Booking.objects.filter(project=p).exists())

    def test_seed_demo_refuses_without_debug(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command("seed_demo")
