from django.contrib import admin, messages

from apps.core.admin import AuditedModelAdmin, run_service_action
from apps.core.audit import log_action
from apps.payments import services as pay

from . import services
from .models import Booking, CustomerRequest, Quotation, QuotationLine, VendorInquiry


class InquiryInline(admin.TabularInline):
    model = VendorInquiry
    extra = 0
    autocomplete_fields = ("vendor",)
    fields = ("vendor", "package", "ceremony", "status", "channel", "quoted_price_rwf", "quote_valid_until", "evidence_reference", "internal_notes")


@admin.register(CustomerRequest)
class CustomerRequestAdmin(AuditedModelAdmin):
    list_display = ("id", "project", "customer_email", "kind", "status", "assigned_to", "created_at")
    list_filter = ("status", "kind", "assigned_to")
    search_fields = ("project__title", "project__customer__email", "contact_phone")
    readonly_fields = ("project", "kind", "message", "contact_phone", "preferred_contact", "consent_vendor_outreach", "created_at")
    inlines = [InquiryInline]
    actions = ["assign_to_me", "mark_in_review", "mark_vendor_outreach", "mark_quotation_prep", "make_draft_quotation", "close_requests"]

    @admin.display(description="Customer")
    def customer_email(self, obj):
        return obj.project.customer.email

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("project__customer", "assigned_to")

    @admin.action(description="Assign to me")
    def assign_to_me(self, request, queryset):
        for r in queryset:
            r.assigned_to = request.user
            r.save(update_fields=["assigned_to", "updated_at"])
            log_action(request.user, "request_assigned", r, request=request)

    def _status(self, request, queryset, status):
        from apps.planning.models import WeddingProject
        for r in queryset:
            r.status = status
            r.save(update_fields=["status", "updated_at"])
            if status in (CustomerRequest.Status.IN_REVIEW, CustomerRequest.Status.VENDOR_OUTREACH, CustomerRequest.Status.QUOTATION_PREP):
                WeddingProject.objects.filter(pk=r.project_id, status=WeddingProject.Status.REQUESTED).update(status=WeddingProject.Status.IN_PROGRESS)
            log_action(request.user, f"request_{status}", r, request=request)

    @admin.action(description="Mark: in review")
    def mark_in_review(self, request, queryset):
        self._status(request, queryset, CustomerRequest.Status.IN_REVIEW)

    @admin.action(description="Mark: contacting vendors")
    def mark_vendor_outreach(self, request, queryset):
        self._status(request, queryset, CustomerRequest.Status.VENDOR_OUTREACH)

    @admin.action(description="Mark: preparing quotation")
    def mark_quotation_prep(self, request, queryset):
        self._status(request, queryset, CustomerRequest.Status.QUOTATION_PREP)

    @admin.action(description="Create a draft quotation from the customer's plan")
    def make_draft_quotation(self, request, queryset):
        if not request.user.has_perm("operations.add_quotation"):
            self.message_user(request, "You may not create quotations.", messages.ERROR)
            return
        for r in queryset:
            if not r.project.plan_items.exists():
                self.message_user(request, f"{r}: the plan has no items yet.", messages.ERROR)
                continue
            q = services.build_draft_quotation(r.project, r, request.user)
            self.message_user(request, f"Draft quotation v{q.version} created for {r.project.title}. Review its lines, set a validity date, then send.")

    @admin.action(description="Close request")
    def close_requests(self, request, queryset):
        self._status(request, queryset, CustomerRequest.Status.CLOSED)


@admin.register(VendorInquiry)
class VendorInquiryAdmin(AuditedModelAdmin):
    list_display = ("vendor", "request", "status", "channel", "quoted_price_rwf", "quote_valid_until", "contacted_at")
    list_filter = ("status", "channel")
    autocomplete_fields = ("vendor",)
    search_fields = ("vendor__name",)


class LineInline(admin.TabularInline):
    model = QuotationLine
    extra = 0
    fields = ("ceremony", "category", "vendor", "package", "vendor_name", "description", "quantity", "unit_price_rwf", "line_total_rwf", "price_status", "private_vendor_cost_rwf", "internal_notes")
    readonly_fields = ("line_total_rwf",)

    def _editable(self, obj):
        return obj is None or obj.status in (Quotation.Status.DRAFT, Quotation.Status.INTERNAL_REVIEW)

    def has_add_permission(self, request, obj=None):
        return self._editable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._editable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._editable(obj) and super().has_delete_permission(request, obj)


@admin.register(Quotation)
class QuotationAdmin(AuditedModelAdmin):
    list_display = ("__str__", "status", "valid_until", "subtotal_rwf", "fee_rwf", "payable_total_rwf", "estimated_total_rwf", "sent_at")
    list_filter = ("status",)
    search_fields = ("project__title", "project__customer__email")
    readonly_fields = ("project", "request", "version", "status", "fee_bps", "subtotal_rwf", "fee_rwf", "contingency_rwf",
                       "payable_total_rwf", "estimated_total_rwf", "sent_at", "responded_at", "created_by")
    inlines = [LineInline]
    actions = ["to_review", "send_to_customer", "invoice_remaining"]
    fieldsets = (
        (None, {"fields": ("project", "request", "version", "status", "valid_until")}),
        ("Totals (integer RWF)", {"fields": ("subtotal_rwf", "fee_bps", "fee_rwf", "payable_total_rwf", "contingency_rwf", "estimated_total_rwf")}),
        ("Shown to customer", {"fields": ("notes_for_customer",)}),
        ("PRIVATE", {"fields": ("internal_notes",)}),
        ("History", {"fields": ("sent_at", "responded_at", "created_by")}),
    )

    def has_add_permission(self, request):
        return False  # create drafts from a request ("Create a draft quotation") so lines are snapshotted

    @admin.action(description="Move to internal review")
    def to_review(self, request, queryset):
        run_service_action(self, request, queryset, lambda q: services.move_quotation_to_review(q, request.user), "Moved to review")

    @admin.action(description="Send to customer (validity date required)")
    def send_to_customer(self, request, queryset):
        run_service_action(self, request, queryset, lambda q: services.send_quotation(q, request.user), "Sent")

    @admin.action(description="Create invoice for the remaining payable amount (accepted quotations only)")
    def invoice_remaining(self, request, queryset):
        if not request.user.has_perm("payments.add_invoice"):
            self.message_user(request, "You may not create invoices.", messages.ERROR)
            return
        run_service_action(self, request, queryset, lambda q: pay.create_invoice(q, request.user), "Invoices created (draft)")


@admin.register(Booking)
class BookingAdmin(AuditedModelAdmin):
    list_display = ("id", "description", "vendor_name", "project", "service_date", "agreed_price_rwf", "status", "confirmed_at")
    list_filter = ("status",)
    search_fields = ("vendor_name", "project__title", "description")
    readonly_fields = ("project", "quotation_line", "vendor", "ceremony", "vendor_name", "description", "agreed_price_rwf", "status", "confirmed_at", "confirmed_by")
    actions = ["confirm", "complete", "cancel"]

    def has_add_permission(self, request):
        return False  # bookings are created when a customer accepts a quotation

    @admin.action(description="Confirm booking (needs vendor-confirmation evidence saved on the booking)")
    def confirm(self, request, queryset):
        run_service_action(self, request, queryset, lambda b: services.confirm_booking(b, request.user, b.confirmation_evidence), "Confirmed")

    @admin.action(description="Mark completed")
    def complete(self, request, queryset):
        run_service_action(self, request, queryset, lambda b: services.change_booking_status(b, Booking.Status.COMPLETED, request.user), "Completed")

    @admin.action(description="Cancel booking")
    def cancel(self, request, queryset):
        run_service_action(self, request, queryset, lambda b: services.change_booking_status(b, Booking.Status.CANCELLED, request.user), "Cancelled")
