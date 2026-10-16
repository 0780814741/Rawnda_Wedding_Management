from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from apps.core.admin import AuditedModelAdmin

from .models import CustomerProfile, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "role", "is_staff", "is_active")
    list_filter = ("role", "is_staff", "is_superuser", "is_active", "groups")
    search_fields = ("email", "first_name", "last_name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "role")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "first_name", "last_name", "password1", "password2", "is_staff", "groups")}),
    )

    def save_model(self, request, obj, form, change):
        from apps.core.audit import log_action
        super().save_model(request, obj, form, change)
        log_action(request.user, "user_saved", obj, changes={"changed": list(form.changed_data)}, request=request)


@admin.register(CustomerProfile)
class CustomerProfileAdmin(AuditedModelAdmin):
    list_display = ("user", "phone", "consent_terms_at", "deletion_requested_at")
    list_filter = ("deletion_requested_at",)
    search_fields = ("user__email", "phone")
    readonly_fields = ("consent_terms_at",)
