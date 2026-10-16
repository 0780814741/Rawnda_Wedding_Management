from django.conf import settings

from .models import Notification


def site(request):
    unread = 0
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        unread = Notification.objects.filter(user=user, read_at__isnull=True).count()
    return {
        "SITE_NAME": settings.SITE_NAME,
        "SUPPORT_PHONE": settings.SUPPORT_PHONE,
        "PLATFORM_SERVICE_FEE_PERCENT": settings.PLATFORM_SERVICE_FEE_BPS / 100,
        "unread_notifications": unread,
        "DEBUG_DEMO": settings.SHOW_DEMO_VENDORS,
    }
