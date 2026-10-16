from datetime import date, timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import CustomerProfile
from apps.core.models import AuditLog
from apps.operations import services as ops
from apps.operations.models import Quotation
from apps.vendors.models import UploadedMedia, Vendor
from apps.vendors.validators import validate_portfolio_image

from .helpers import PASSWORD, make_customer, make_package, make_project, make_staff, make_vendor


class AuthTests(TestCase):
    def test_signup_creates_customer_profile_and_logs_in(self):
        r = self.client.post(reverse("accounts:signup"), {
            "first_name": "Eric", "email": "Eric@Example.com", "password1": PASSWORD, "password2": PASSWORD,
            "phone": "0788111222", "accept_terms": "on"})
        self.assertRedirects(r, reverse("planning:new"))
        u = CustomerProfile.objects.get(phone="0788111222").user
        self.assertEqual(u.email, "eric@example.com")
        self.assertFalse(u.is_staff)
        self.assertNotEqual(u.password, PASSWORD)  # hashed

    def test_signup_requires_consent_and_unique_email(self):
        make_customer("dup@example.com")
        r = self.client.post(reverse("accounts:signup"), {"first_name": "X", "email": "dup@example.com", "password1": PASSWORD, "password2": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Please confirm to continue")
        self.assertContains(r, "already exists")

    def test_login_and_logout(self):
        make_customer()
        self.assertTrue(self.client.login(email="alice@example.com", password=PASSWORD))
        r = self.client.post(reverse("accounts:logout"))
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get(reverse("planning:dashboard")).status_code, 302)

    def test_anonymous_redirected_from_private_pages(self):
        p = make_project(make_customer())
        for name, args in [("planning:dashboard", []), ("planning:plan", [p.pk]), ("planning:project_dashboard", [p.pk]), ("planning:request_plan", [p.pk])]:
            r = self.client.get(reverse(name, args=args))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn("/accounts/login/", r["Location"])

    def test_rate_limit_blocks_login_flood(self):
        with override_settings(RATELIMIT_ENABLED=True):
            from django.core.cache import cache
            cache.clear()
            codes = [self.client.post(reverse("accounts:login"), {"username": "x@example.com", "password": "bad"}).status_code for _ in range(25)]
        self.assertIn(429, codes)
        cache.clear()


class AuthorizationTests(TestCase):
    def setUp(self):
        self.alice, self.bob = make_customer("alice@example.com"), make_customer("bob@example.com", first_name="Bob")
        self.project = make_project(self.alice)
        v = make_vendor(); make_package(v)
        from apps.planning import services
        opts = services.generate_options(self.project)
        services.apply_option(self.project, opts[0])
        self.client.login(email="bob@example.com", password=PASSWORD)

    def test_other_customer_gets_404_everywhere(self):
        pk = self.project.pk
        for name in ("planning:plan", "planning:project_dashboard", "planning:request_plan", "planning:setup_basics", "planning:setup_ceremonies", "planning:setup_preferences"):
            self.assertEqual(self.client.get(reverse(name, args=[pk])).status_code, 404, name)
        self.assertEqual(self.client.post(reverse("planning:apply_option", args=[pk]), {"strategy": "budget"}).status_code, 404)

    def test_other_customer_cannot_respond_to_quotation(self):
        q = ops.build_draft_quotation(self.project)
        r = self.client.post(reverse("planning:quotation_respond", args=[self.project.pk, q.pk]), {"action": "accept"})
        self.assertEqual(r.status_code, 404)

    def test_owner_can_view(self):
        self.client.logout(); self.client.login(email="alice@example.com", password=PASSWORD)
        self.assertEqual(self.client.get(reverse("planning:plan", args=[self.project.pk])).status_code, 200)

    def test_customer_cannot_open_admin(self):
        r = self.client.get("/ops-admin/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("login", r["Location"])

    def test_nonstaff_service_calls_denied(self):
        q = ops.build_draft_quotation(self.project)
        with self.assertRaises(PermissionError):
            ops.send_quotation(q, self.bob)


class VendorPrivacyTests(TestCase):
    def setUp(self):
        self.v = make_vendor("Public Vendor", contact_phone="0788999999", contact_email="private-contact@example.com", internal_notes="TOP SECRET NOTE")
        self.pkg = make_package(self.v, internal_notes="SUPPLIER COST NOTE", private_supplier_price_rwf=123_456)

    def test_public_pages_never_leak_private_fields(self):
        for url in (reverse("vendors:list"), reverse("vendors:detail", args=[self.v.slug]), reverse("core:home")):
            body = self.client.get(url).content.decode()
            for secret in ("0788999999", "private-contact@example.com", "TOP SECRET NOTE", "SUPPLIER COST NOTE", "123456", "123,456"):
                self.assertNotIn(secret, body, url)

    def test_contact_shown_only_when_explicitly_approved(self):
        self.v.public_contact_approved = True; self.v.save()
        self.assertContains(self.client.get(reverse("vendors:detail", args=[self.v.slug])), "0788999999")

    def test_unapproved_suspended_and_demo_hidden(self):
        draft = make_vendor("Draft One", approved=False)
        susp = make_vendor("Suspended One"); susp.status = Vendor.Status.SUSPENDED; susp.save()
        demo = make_vendor("Demo One", is_demo=True)
        body = self.client.get(reverse("vendors:list")).content.decode()
        self.assertIn("Public Vendor", body)
        for name in ("Draft One", "Suspended One", "Demo One"):
            self.assertNotIn(name, body)
        for v in (draft, susp, demo):
            self.assertEqual(self.client.get(reverse("vendors:detail", args=[v.slug])).status_code, 404)

    @override_settings(SHOW_DEMO_VENDORS=True)
    def test_demo_visible_only_with_flag_and_labelled(self):
        make_vendor("Demo Two", is_demo=True)
        self.assertContains(self.client.get(reverse("vendors:list")), "Demo (fictional)")

    def test_filters_and_htmx_partial(self):
        r = self.client.get(reverse("vendors:list"), {"category": "catering", "district": "gasabo", "guests": "50"}, headers={"HX-Request": "true"})
        self.assertContains(r, "Public Vendor"); self.assertNotContains(r, "<html")
        self.assertContains(self.client.get(reverse("vendors:list"), {"district": "musanze"}), "No vendors match")

    def test_customer_quotation_view_hides_private_fields(self):
        c = make_customer(); p = make_project(c)
        from apps.planning import services
        services.apply_option(p, services.generate_options(p)[0])
        q = ops.build_draft_quotation(p)
        q.internal_notes = "QUOTE INTERNAL"; q.notes_for_customer = "Hello customer"; q.valid_until = date.today() + timedelta(days=5); q.save()
        line = q.lines.first()
        Quotation.objects.filter(pk=q.pk).update(status="draft")
        line.private_vendor_cost_rwf = 777_001; line.internal_notes = "LINE INTERNAL"; line.save()
        ops.send_quotation(q, make_staff())
        self.client.login(email=c.email, password=PASSWORD)
        body = self.client.get(reverse("planning:project_dashboard", args=[p.pk])).content.decode()
        self.assertIn("Hello customer", body)
        for secret in ("QUOTE INTERNAL", "LINE INTERNAL", "777001", "777,001", "SECRET NEGOTIATION", "secret-vendor@example.com"):
            self.assertNotIn(secret, body)


class UploadValidationTests(TestCase):
    def _png(self, name="a.png"):
        import io
        from PIL import Image
        buf = io.BytesIO(); Image.new("RGB", (10, 10), "red").save(buf, "PNG")
        return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")

    def test_valid_image_ok(self):
        validate_portfolio_image(self._png())

    def test_fake_image_rejected(self):
        with self.assertRaises(ValidationError):
            validate_portfolio_image(SimpleUploadedFile("evil.png", b"<script>alert(1)</script>", content_type="image/png"))

    def test_bad_extension_rejected(self):
        with self.assertRaises(ValidationError):
            validate_portfolio_image(self._png("shell.php"))

    @override_settings(MAX_UPLOAD_BYTES=10)
    def test_oversize_rejected(self):
        with self.assertRaises(ValidationError):
            validate_portfolio_image(self._png())

    @override_settings(MEDIA_UPLOADS_ENABLED=False)
    def test_uploads_disabled_without_persistent_storage(self):
        m = UploadedMedia(vendor=make_vendor(), image=self._png())
        with self.assertRaises(ValidationError):
            m.clean()


class VendorAdminPermissionTests(TestCase):
    def test_planner_cannot_approve_but_manager_can(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory
        v = make_vendor("To Approve", approved=False)
        admin_obj = site._registry[Vendor]
        for groups, expect in ((("Planner",), Vendor.Status.DRAFT), (("Operations Manager",), Vendor.Status.APPROVED)):
            u = make_staff(f"{groups[0][:3]}@example.com", groups)
            req = RequestFactory().post("/"); req.user = u; req.session = {}; req._messages = type("M", (), {"add": lambda *a, **k: None})()
            admin_obj.approve(req, Vendor.objects.filter(pk=v.pk))
            v.refresh_from_db()
            self.assertEqual(v.status, expect, groups)
        self.assertTrue(AuditLog.objects.filter(action="vendor_approved").exists())

    def test_audit_log_is_immutable(self):
        e = AuditLog.objects.create(action="x", object_type="t")
        with self.assertRaises(PermissionError):
            e.save()
        with self.assertRaises(PermissionError):
            e.delete()
