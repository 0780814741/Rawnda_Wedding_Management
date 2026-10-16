"""Helpers for writing audit entries. Never pass secrets in `changes`."""
import logging

from .models import AuditLog

logger = logging.getLogger("audit")


def client_ip(request):
    if request is None:
        return None
    # Render sets X-Forwarded-For; the left-most value is the client.
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip() or None
    return request.META.get("REMOTE_ADDR")


def log_action(actor, action, obj=None, changes=None, request=None, object_type="", object_id="", object_repr=""):
    entry = AuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        actor_label=(getattr(actor, "email", "") or getattr(actor, "username", "")) if actor else "system",
        action=action,
        object_type=object_type or (obj._meta.label if obj is not None else ""),
        object_id=str(object_id or (obj.pk if obj is not None else "")),
        object_repr=(object_repr or (str(obj) if obj is not None else ""))[:255],
        changes=changes or {},
        ip_address=client_ip(request),
    )
    logger.info("audit action=%s object=%s id=%s actor=%s", action, entry.object_type, entry.object_id, entry.actor_label)
    return entry


def diff_instances(old: dict, new: dict) -> dict:
    return {k: [old.get(k), new.get(k)] for k in new if old.get(k) != new.get(k)}
