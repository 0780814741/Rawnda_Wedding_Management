from django.contrib import admin

from .audit import diff_instances, log_action
from .models import AuditLog, Notification


class AuditedModelAdmin(admin.ModelAdmin):
    """Writes an AuditLog entry for every create/change/delete done through the admin."""

    audit_exclude = ()

    def _snapshot(self, obj):
        data = {}
        for f in obj._meta.concrete_fields:
            if f.name in self.audit_exclude or f.name in ("password",):
                continue
            data[f.name] = str(getattr(obj, f.attname, ""))
        return data

    def save_model(self, request, obj, form, change):
        old = {}
        if change and obj.pk:
            prev = type(obj).objects.filter(pk=obj.pk).first()
            old = self._snapshot(prev) if prev else {}
        super().save_model(request, obj, form, change)
        new = self._snapshot(obj)
        log_action(
            request.user, "admin_update" if change else "admin_create", obj,
            changes=diff_instances(old, new) if change else {}, request=request,
        )

    def delete_model(self, request, obj):
        log_action(request.user, "admin_delete", obj, request=request)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            log_action(request.user, "admin_delete", obj, request=request)
        super().delete_queryset(request, queryset)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_label", "action", "object_type", "object_id", "object_repr")
    list_filter = ("action", "object_type")
    search_fields = ("actor_label", "object_repr", "object_id")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(AuditedModelAdmin):
    list_display = ("user", "kind", "title", "created_at", "read_at")
    list_filter = ("kind",)
    search_fields = ("title", "user__email")


def run_service_action(modeladmin, request, queryset, fn, success):
    """Run a workflow service function per object; show errors as admin messages instead of crashing."""
    from django.contrib import messages
    from django.core.exceptions import ValidationError

    from apps.operations.models import InvalidTransition

    ok = 0
    for obj in queryset:
        try:
            fn(obj)
            ok += 1
        except (ValidationError, InvalidTransition, PermissionError) as e:
            msg = e.messages[0] if isinstance(e, ValidationError) else str(e)
            modeladmin.message_user(request, f"{obj}: {msg}", messages.ERROR)
    if ok:
        modeladmin.message_user(request, f"{success}: {ok}.", messages.SUCCESS)
