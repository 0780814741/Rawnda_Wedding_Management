from django import forms
from django.contrib import admin

from apps.core.admin import AuditedModelAdmin, run_service_action
from apps.core.audit import log_action

from . import services
from .models import CommissionRecord, Invoice, PaymentRecord, RefundRecord, VendorSettlementRecord


class PaymentInline(admin.TabularInline):
    model = PaymentRecord
    extra = 0
    fields = ("amount_rwf", "method", "status", "reference", "received_at", "evidence_note")
    readonly_fields = ("status",)
    show_change_link = True


@admin.register(Invoice)
class InvoiceAdmin(AuditedModelAdmin):
    list_display = ("number", "project", "kind", "amount_rwf", "paid", "status", "due_date")
    list_filter = ("status", "kind")
    search_fields = ("number", "project__title", "project__customer__email")
    readonly_fields = ("number", "project", "quotation", "kind", "amount_rwf", "currency", "status", "issued_at", "issued_by")
    inlines = [PaymentInline]
    actions = ["issue", "void"]

    @admin.display(description="Paid (verified)")
    def paid(self, obj):
        return f"RWF {obj.paid_rwf:,}"

    def has_add_permission(self, request):
        return False  # create via "Create invoice" on an accepted quotation

    @admin.action(description="Issue invoice to customer")
    def issue(self, request, queryset):
        run_service_action(self, request, queryset, lambda i: services.issue_invoice(i, request.user), "Issued")

    @admin.action(description="Void invoice")
    def void(self, request, queryset):
        run_service_action(self, request, queryset, lambda i: services.void_invoice(i, request.user), "Voided")


class PaymentForm(forms.ModelForm):
    class Meta:
        model = PaymentRecord
        fields = ["invoice", "amount_rwf", "method", "reference", "received_at", "evidence_note"]

    def clean(self):
        cd = super().clean()
        inv = cd.get("invoice")
        if inv and not self.instance.pk and inv.status not in (Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID):
            raise forms.ValidationError("Payments can only be recorded against an issued invoice.")
        return cd


@admin.register(PaymentRecord)
class PaymentRecordAdmin(AuditedModelAdmin):
    form = PaymentForm
    list_display = ("invoice", "amount_rwf", "method", "status", "reference", "received_at", "verified_by")
    list_filter = ("status", "method")
    search_fields = ("invoice__number", "reference")
    readonly_fields = ("status", "verified_by", "verified_at", "recorded_by", "provider", "external_id")
    actions = ["verify", "reject", "reverse"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.recorded_by = request.user
            obj.status = PaymentRecord.Status.PENDING
        super().save_model(request, obj, form, change)

    @admin.action(description="Verify payment (after checking the bank/mobile-money statement)")
    def verify(self, request, queryset):
        run_service_action(self, request, queryset, lambda p: services.verify_payment(p, request.user), "Verified")

    @admin.action(description="Reject payment (not received)")
    def reject(self, request, queryset):
        run_service_action(self, request, queryset, lambda p: services.reject_payment(p, request.user), "Rejected")

    @admin.action(description="Reverse a verified payment")
    def reverse(self, request, queryset):
        run_service_action(self, request, queryset, lambda p: services.reverse_payment(p, request.user), "Reversed")


@admin.register(RefundRecord)
class RefundRecordAdmin(AuditedModelAdmin):
    list_display = ("payment", "amount_rwf", "status", "reason", "processed_at")
    list_filter = ("status",)
    readonly_fields = ("status", "approved_by", "processed_at")
    actions = ["approve", "processed"]

    @admin.action(description="Approve refund")
    def approve(self, request, queryset):
        run_service_action(self, request, queryset, lambda r: services.approve_refund(r, request.user), "Approved")

    @admin.action(description="Mark refund as sent (needs reference saved on the refund)")
    def processed(self, request, queryset):
        run_service_action(self, request, queryset, lambda r: services.mark_refund_processed(r, request.user, r.reference), "Marked as sent")


@admin.register(CommissionRecord)
class CommissionRecordAdmin(AuditedModelAdmin):
    list_display = ("booking", "rate_bps", "base_amount_rwf", "commission_rwf", "status")
    list_filter = ("status",)
    readonly_fields = ("booking", "rate_bps", "base_amount_rwf", "commission_rwf")

    def has_add_permission(self, request):
        return False


@admin.register(VendorSettlementRecord)
class VendorSettlementAdmin(AuditedModelAdmin):
    list_display = ("booking", "gross_amount_rwf", "commission_rwf", "provider_fees_rwf", "net", "status", "paid_at")
    list_filter = ("status",)
    readonly_fields = ("booking", "gross_amount_rwf", "commission_rwf", "status", "paid_at")
    actions = ["schedule", "paid"]

    @admin.display(description="Net payable to vendor")
    def net(self, obj):
        return f"RWF {obj.net_payable_rwf:,}"

    def has_add_permission(self, request):
        return False

    def _guard(self, request, s, allowed):
        if not request.user.has_perm("payments.manage_settlement"):
            raise PermissionError("You may not manage settlements.")
        if s.status not in allowed:
            raise PermissionError(f"Settlement is '{s.get_status_display()}'; it must be due before this step.")

    @admin.action(description="Mark scheduled (only when due)")
    def schedule(self, request, queryset):
        def fn(s):
            self._guard(request, s, {VendorSettlementRecord.Status.DUE})
            s.status = VendorSettlementRecord.Status.SCHEDULED
            s.save(update_fields=["status", "updated_at"])
            log_action(request.user, "settlement_scheduled", s, request=request)
        run_service_action(self, request, queryset, fn, "Scheduled")

    @admin.action(description="Mark paid to vendor (needs payment reference saved)")
    def paid(self, request, queryset):
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        def fn(s):
            self._guard(request, s, {VendorSettlementRecord.Status.DUE, VendorSettlementRecord.Status.SCHEDULED})
            if not s.payment_reference.strip():
                raise ValidationError("Save the payment reference first.")
            s.status, s.paid_at = VendorSettlementRecord.Status.PAID, timezone.now()
            s.save(update_fields=["status", "paid_at", "updated_at"])
            log_action(request.user, "settlement_paid", s, request=request)
        run_service_action(self, request, queryset, fn, "Marked paid")
