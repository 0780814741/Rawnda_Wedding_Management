from django.contrib import admin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse

from apps.core.admin import AuditedModelAdmin

from . import services
from .models import Ceremony, PlanItem, RequiredService, WeddingProject


class CeremonyInline(admin.TabularInline):
    model = Ceremony
    extra = 0


class PlanItemInline(admin.TabularInline):
    model = PlanItem
    extra = 0
    fields = ("ceremony", "category", "package", "vendor_name", "package_name", "quantity", "unit_price_rwf", "line_total_rwf", "price_status", "source")
    readonly_fields = ("line_total_rwf",)


@admin.register(WeddingProject)
class WeddingProjectAdmin(AuditedModelAdmin):
    list_display = ("title", "customer", "wedding_date", "district", "guest_count", "budget_rwf", "status", "created_at")
    list_filter = ("status", "district", "wedding_date")
    search_fields = ("title", "customer__email", "customer__first_name")
    date_hierarchy = "wedding_date"
    readonly_fields = ("customer", "fee_bps", "contingency_bps", "created_at", "updated_at")
    inlines = [CeremonyInline, PlanItemInline]
    actions = ["compare_options"]

    def get_urls(self):
        return [path("<int:pk>/compare/", self.admin_site.admin_view(self.compare_view), name="planning_weddingproject_compare")] + super().get_urls()

    @admin.action(description="Compare planner options for the selected wedding (opens first selected)")
    def compare_options(self, request, queryset):
        project = queryset.first()
        return redirect(reverse("admin:planning_weddingproject_compare", args=[project.pk]))

    def compare_view(self, request, pk):
        project = get_object_or_404(WeddingProject, pk=pk)
        if not self.has_view_permission(request, project):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied
        ctx = {**self.admin_site.each_context(request), "title": f"Planner options: {project.title}", "project": project,
               "options": services.generate_options(project), "opts": self.model._meta}
        return render(request, "admin/planning/compare.html", ctx)


@admin.register(Ceremony)
class CeremonyAdmin(AuditedModelAdmin):
    list_display = ("project", "ceremony_type", "event_date", "guest_count", "district")
    list_filter = ("ceremony_type",)


@admin.register(PlanItem)
class PlanItemAdmin(AuditedModelAdmin):
    list_display = ("project", "ceremony", "category", "vendor_name", "package_name", "line_total_rwf", "price_status", "source")
    list_filter = ("price_status", "source", "category")
    search_fields = ("vendor_name", "package_name", "project__title")
    readonly_fields = ("line_total_rwf",)


@admin.register(RequiredService)
class RequiredServiceAdmin(admin.ModelAdmin):
    list_display = ("ceremony", "category", "is_essential", "is_priority", "allocated_rwf")
    list_filter = ("is_essential", "category")

    def has_add_permission(self, request):
        return False
