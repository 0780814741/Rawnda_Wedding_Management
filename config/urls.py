from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Wedding Planner Operations"
admin.site.site_title = "Operations"
admin.site.index_title = "Private operations dashboard"

handler404 = "apps.core.views.error_404"
handler500 = "apps.core.views.error_500"

urlpatterns = [
    path("", include("apps.core.urls")),
    path("ops-admin/", admin.site.urls),  # non-default path; still protected by auth + permissions
    path("accounts/", include("apps.accounts.urls")),
    path("vendors/", include("apps.vendors.urls")),
    path("plan/", include("apps.planning.urls")),
    path("payments/", include("apps.payments.urls")),
]
if settings.DEBUG and not settings.MEDIA_STORAGE_CONFIGURED:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
