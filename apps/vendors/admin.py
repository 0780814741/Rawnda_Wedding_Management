from django import forms
from django.contrib import admin, messages
from django.utils import timezone

from apps.core.audit import log_action
from apps.core.admin import AuditedModelAdmin

from .constants import CeremonyType
from .models import (AvailabilityCheck, PackageAddOn, PackageInclusion, PriceRecord, UploadedMedia, Vendor,
                     VendorCategory, VendorPackage, VendorServiceArea)


@admin.register(VendorCategory)
class VendorCategoryAdmin(AuditedModelAdmin):
    list_display = ("name", "slug", "ordering", "is_active")
    prepopulated_fields = {"slug": ("name",)}
    list_editable = ("ordering", "is_active")


class ServiceAreaInline(admin.TabularInline):
    model = VendorServiceArea
    extra = 1


class MediaInline(admin.TabularInline):
    model = UploadedMedia
    extra = 0
    fields = ("image", "caption", "alt_text", "is_public")


@admin.register(Vendor)
class VendorAdmin(AuditedModelAdmin):
    list_display = ("name", "status", "is_verified", "verified_at", "category_list", "is_demo", "updated_at")
    list_filter = ("status", "is_verified", "is_demo", "categories", "service_areas__district")
    search_fields = ("name", "contact_name", "contact_phone", "contact_email")
    filter_horizontal = ("categories",)
    inlines = [ServiceAreaInline, MediaInline]
    readonly_fields = ("status", "verified_by", "created_at", "updated_at")
    actions = ["approve", "suspend", "archive", "verify"]
    fieldsets = (
        ("Public profile", {"fields": ("name", "slug", "description", "categories", "status", "is_demo")}),
        ("Verification", {"fields": ("is_verified", "verified_at", "verified_by", "verification_notes")}),
        ("PRIVATE contact & notes (never shown to customers)", {
            "fields": ("contact_name", "contact_phone", "contact_email", "public_contact_approved", "internal_notes")}),
        ("Later phase", {"classes": ("collapse",), "fields": ("portal_user",)}),
        ("Timestamps", {"classes": ("collapse",), "fields": ("created_at", "updated_at")}),
    )

    @admin.display(description="Categories")
    def category_list(self, obj):
        return ", ".join(c.name for c in obj.categories.all())

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("categories")

    def _set_status(self, request, queryset, new_status, label):
        if not request.user.has_perm("vendors.approve_vendor"):
            self.message_user(request, "You do not have permission to change vendor status.", messages.ERROR)
            return
        for v in queryset:
            old = v.status
            Vendor.objects.filter(pk=v.pk).update(status=new_status, updated_at=timezone.now())
            log_action(request.user, f"vendor_{label}", v, changes={"status": [old, new_status]}, request=request)
        self.message_user(request, f"{queryset.count()} vendor(s) {label}.")

    @admin.action(description="Approve selected vendors (make public)")
    def approve(self, request, queryset):
        self._set_status(request, queryset, Vendor.Status.APPROVED, "approved")

    @admin.action(description="Suspend selected vendors (hide from customers)")
    def suspend(self, request, queryset):
        self._set_status(request, queryset, Vendor.Status.SUSPENDED, "suspended")

    @admin.action(description="Archive selected vendors")
    def archive(self, request, queryset):
        self._set_status(request, queryset, Vendor.Status.ARCHIVED, "archived")

    @admin.action(description="Mark selected vendors as verified today")
    def verify(self, request, queryset):
        if not request.user.has_perm("vendors.verify_vendor"):
            self.message_user(request, "You do not have permission to verify vendors.", messages.ERROR)
            return
        for v in queryset:
            Vendor.objects.filter(pk=v.pk).update(is_verified=True, verified_at=timezone.localdate(), verified_by=request.user)
            log_action(request.user, "vendor_verified", v, request=request)
        self.message_user(request, f"{queryset.count()} vendor(s) verified.")


class InclusionInline(admin.TabularInline):
    model = PackageInclusion
    extra = 2


class AddOnInline(admin.TabularInline):
    model = PackageAddOn
    extra = 0


class PriceRecordInline(admin.TabularInline):
    model = PriceRecord
    extra = 0
    can_delete = False
    readonly_fields = ("price_rwf", "pricing_unit", "price_status", "source", "recorded_at", "valid_until", "note")

    def has_add_permission(self, request, obj=None):
        return False


class PackageForm(forms.ModelForm):
    ceremony_types = forms.MultipleChoiceField(
        choices=CeremonyType.choices, required=False, widget=forms.CheckboxSelectMultiple,
        help_text="Leave all unticked if the package suits any ceremony.",
    )

    class Meta:
        model = VendorPackage
        fields = "__all__"


@admin.register(VendorPackage)
class VendorPackageAdmin(AuditedModelAdmin):
    form = PackageForm
    list_display = ("name", "vendor", "category", "pricing_unit", "price_rwf", "price_status", "last_price_verified_at", "quote_valid_until", "is_active")
    list_filter = ("category", "price_status", "pricing_unit", "is_active", "vendor__status")
    search_fields = ("name", "vendor__name")
    autocomplete_fields = ("vendor",)
    inlines = [InclusionInline, AddOnInline, PriceRecordInline]
    fieldsets = (
        (None, {"fields": ("vendor", "category", "name", "description", "ceremony_types", "is_active")}),
        ("Price & capacity", {"fields": ("pricing_unit", "price_rwf", "price_status", "last_price_verified_at", "quote_valid_until", "min_guests", "max_guests")}),
        ("PRIVATE (never shown to customers)", {"fields": ("private_supplier_price_rwf", "internal_notes")}),
    )


@admin.register(AvailabilityCheck)
class AvailabilityCheckAdmin(AuditedModelAdmin):
    list_display = ("vendor", "event_date", "status", "checked_at", "checked_by")
    list_filter = ("status",)
    autocomplete_fields = ("vendor",)
    date_hierarchy = "event_date"

    def save_model(self, request, obj, form, change):
        if "status" in form.changed_data and obj.status != AvailabilityCheck.Status.NOT_CHECKED:
            obj.checked_at, obj.checked_by = timezone.now(), request.user
        super().save_model(request, obj, form, change)


@admin.register(UploadedMedia)
class UploadedMediaAdmin(AuditedModelAdmin):
    list_display = ("vendor", "caption", "is_public", "created_at")
    autocomplete_fields = ("vendor",)

    def save_model(self, request, obj, form, change):
        obj.uploaded_by = obj.uploaded_by or request.user
        super().save_model(request, obj, form, change)
