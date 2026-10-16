"""Glue between the ORM and the pure budget planner."""
from django.db import transaction

from apps.vendors.models import VendorCategory, VendorPackage

from . import budget
from .models import PlanItem, RequiredService, WeddingProject


def load_package_infos():
    packages = (
        VendorPackage.objects.filter(is_active=True, vendor__in=_public_vendor_ids())
        .select_related("vendor", "category")
        .prefetch_related("vendor__service_areas")
    )
    infos = []
    for p in packages:
        infos.append(budget.PackageInfo(
            id=p.id, vendor_id=p.vendor_id, vendor_name=p.vendor.name, name=p.name, category_slug=p.category.slug,
            pricing_unit=p.pricing_unit, price_rwf=p.price_rwf, min_guests=p.min_guests, max_guests=p.max_guests,
            ceremony_types=tuple(p.ceremony_types or ()),
            districts=frozenset(a.district for a in p.vendor.service_areas.all()),
            price_status=p.price_status, is_price_stale=p.is_price_stale,
        ))
    return infos


def _public_vendor_ids():
    from apps.vendors.models import Vendor
    return Vendor.objects.public().values("id")


def plan_input_for(project: WeddingProject) -> budget.PlanInput:
    ceremonies = tuple(
        budget.CeremonyInput(c.ceremony_type, c.effective_guests, c.effective_district)
        for c in project.ceremonies.all()
    )
    return budget.PlanInput(
        budget_rwf=project.budget_rwf or 0, ceremonies=ceremonies, priorities=frozenset(project.priorities or []),
        fee_bps=project.fee_bps, contingency_bps=project.contingency_bps,
    )


def generate_options(project: WeddingProject):
    return budget.build_options(plan_input_for(project), load_package_infos())


@transaction.atomic
def sync_required_services(project: WeddingProject):
    """Recompute the services each ceremony needs (rules only; no prices involved)."""
    categories = {c.slug: c for c in VendorCategory.objects.all()}
    plan = plan_input_for(project)
    spendable = budget.spendable_budget(plan.budget_rwf, plan.fee_bps, plan.contingency_bps)
    cer_alloc = budget.split_by_weights(spendable, {c.code: budget.CEREMONY_WEIGHTS.get(c.code, 10) for c in plan.ceremonies}) if plan.ceremonies else {}
    for cer in project.ceremonies.all():
        rules = budget.CEREMONY_RULES.get(cer.ceremony_type, [])
        priorities = set(project.priorities or [])
        shares = {slug: (share * 3 // 2 if slug in priorities else share) for slug, share, _ in rules}
        alloc = budget.split_by_weights(cer_alloc.get(cer.ceremony_type, 0), shares) if shares else {}
        keep = []
        for slug, share, essential in rules:
            cat = categories.get(slug)
            if not cat:
                continue
            RequiredService.objects.update_or_create(
                ceremony=cer, category=cat,
                defaults={"is_essential": essential, "is_priority": slug in priorities, "allocated_rwf": alloc.get(slug, 0)},
            )
            keep.append(cat.pk)
        cer.required_services.exclude(category_id__in=keep).delete()


@transaction.atomic
def apply_option(project: WeddingProject, option: budget.PlanOption):
    """Replace auto-suggested plan items with the chosen option. Staff-set items are kept."""
    project.plan_items.filter(source=PlanItem.Source.AUTO).delete()
    ceremonies = {c.ceremony_type: c for c in project.ceremonies.all()}
    cats = {c.slug: c for c in VendorCategory.objects.all()}
    for item in option.items:
        pkg = VendorPackage.objects.select_related("vendor").get(pk=item.package.id)
        if project.plan_items.filter(ceremony=ceremonies[item.ceremony], category=cats[item.category]).exists():
            continue  # a staff-set item already covers this slot
        PlanItem.objects.create(
            project=project, ceremony=ceremonies[item.ceremony], category=cats[item.category], package=pkg,
            vendor=pkg.vendor, vendor_name=pkg.vendor.name, package_name=pkg.name, quantity=item.quantity,
            unit_price_rwf=item.unit_price_rwf, price_status=pkg.price_status,
            price_may_be_outdated=pkg.is_price_stale, source=PlanItem.Source.AUTO, strategy=option.strategy,
        )
    return project.plan_items.count()


def project_totals(project: WeddingProject):
    subtotal = sum(i.line_total_rwf for i in project.plan_items.all())
    return budget.plan_totals(subtotal, project.fee_bps, project.contingency_bps, project.budget_rwf or 0)
