from django.conf import settings
from django.core.checks import Warning, register


@register()
def media_storage_check(app_configs, **kwargs):
    if not settings.DEBUG and not settings.TESTING and not settings.MEDIA_STORAGE_CONFIGURED:
        return [
            Warning(
                "Persistent media storage is not configured; vendor image uploads are DISABLED in production.",
                hint="Set AWS_STORAGE_BUCKET_NAME and related variables (S3-compatible storage). See README.",
                id="core.W001",
            )
        ]
    return []
