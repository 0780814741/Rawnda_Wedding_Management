from django.contrib import admin as dj_admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from apps.operations import services as ops
from apps.operations.models import CustomerRequest
from apps.payments import services as pay

from .helpers import PASSWORD, make_customer, make_staff
from .test_workflow import planned_project, sent_quotation

User = get_user_model()


class AdminRenderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.superuser = User.objects.create_superuser(email="root@example.com", password=PASSWORD)
        cls.customer = make_customer()
        cls.project, *_ = planned_project(cls.customer)
        cls.req = CustomerRequest.objects.create(project=cls.project, consent_vendor_outreach=True, contact_phone="1")
        cls.q, cls.staff = sent_quotation(cls.project)
        ops.accept_quotation(cls.q, cls.customer)
        cls.q.refresh_from_db()
        cls.inv = pay.create_invoice(cls.q, cls.staff)
        pay.issue_invoice(cls.inv, cls.staff)
        cls.pay = pay.record_payment(cls.inv, cls.staff, amount_rwf=1000, method="cash", reference="R")

    def setUp(self):
        self.client.force_login(self.superuser)

    def test_every_registered_admin_page_renders(self):
        failures = []
        for model, model_admin in dj_admin.site._registry.items():
            app, name = model._meta.app_label, model._meta.model_name
            urls = [reverse(f"admin:{app}_{name}_changelist")]
            fake_request = RequestFactory().get("/"); fake_request.user = self.superuser
            if model_admin.has_add_permission(fake_request):
                urls.append(reverse(f"admin:{app}_{name}_add"))
            obj = model.objects.first()
            if obj is not None:
                urls.append(reverse(f"admin:{app}_{name}_change", args=[obj.pk]))
            for url in urls:
                r = self.client.get(url)
                if r.status_code != 200:
                    failures.append((url, r.status_code))
        self.assertEqual(failures, [])

    def test_compare_options_view(self):
        r = self.client.get(reverse("admin:planning_weddingproject_compare", args=[self.project.pk]))
        self.assertContains(r, "Rules-based planner output")

    def test_role_permissions(self):
        planner = make_staff("plan@example.com", ("Planner",))
        finance = make_staff("fin2@example.com", ("Finance",))
        self.client.force_login(planner)
        self.assertEqual(self.client.get(reverse("admin:operations_customerrequest_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:payments_paymentrecord_changelist")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:accounts_user_changelist")).status_code, 403)
        self.client.force_login(finance)
        self.assertEqual(self.client.get(reverse("admin:payments_paymentrecord_changelist")).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin:vendors_vendor_changelist")).status_code, 403)

    def test_admin_changes_are_audited(self):
        from apps.core.models import AuditLog
        self.client.post(reverse("admin:operations_customerrequest_changelist"), {"action": "assign_to_me", "_selected_action": [self.req.pk]})
        self.req.refresh_from_db()
        self.assertEqual(self.req.assigned_to, self.superuser)
        self.assertTrue(AuditLog.objects.filter(action="request_assigned").exists())
