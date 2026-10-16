"""Role-based permissions via Django Groups. Created/updated idempotently after each migrate."""
from django.contrib.auth.models import Group, Permission

ROLE_PERMISSIONS = {
    "Planner": {
        "view": ["vendors.vendor", "vendors.vendorpackage", "vendors.vendorcategory", "vendors.vendorservicearea",
                 "vendors.availabilitycheck", "planning.weddingproject", "planning.ceremony", "planning.planitem",
                 "operations.booking", "payments.invoice"],
        "change": ["operations.customerrequest", "operations.vendorinquiry", "operations.quotation",
                   "operations.quotationline", "vendors.availabilitycheck", "planning.planitem"],
        "add": ["operations.vendorinquiry", "operations.quotation", "operations.quotationline",
                "vendors.availabilitycheck", "vendors.pricerecord"],
        "view_extra": ["operations.customerrequest", "operations.vendorinquiry", "operations.quotation",
                       "operations.quotationline", "vendors.pricerecord"],
    },
    "Operations Manager": {
        "view": ["vendors.vendor", "vendors.vendorpackage", "vendors.vendorcategory", "vendors.vendorservicearea",
                 "vendors.availabilitycheck", "vendors.pricerecord", "vendors.uploadedmedia", "planning.weddingproject",
                 "planning.ceremony", "planning.planitem", "operations.customerrequest", "operations.vendorinquiry",
                 "operations.quotation", "operations.quotationline", "operations.booking", "payments.invoice",
                 "payments.paymentrecord"],
        "change": ["vendors.vendor", "vendors.vendorpackage", "vendors.vendorservicearea", "vendors.availabilitycheck",
                   "vendors.uploadedmedia", "operations.customerrequest", "operations.vendorinquiry",
                   "operations.quotation", "operations.quotationline", "operations.booking", "planning.planitem"],
        "add": ["vendors.vendor", "vendors.vendorpackage", "vendors.vendorservicearea", "vendors.availabilitycheck",
                "vendors.uploadedmedia", "vendors.pricerecord", "operations.vendorinquiry", "operations.quotation",
                "operations.quotationline"],
        "custom": ["vendors.approve_vendor", "vendors.verify_vendor", "operations.send_quotation",
                   "operations.confirm_booking"],
    },
    "Finance": {
        "view": ["operations.booking", "operations.quotation", "operations.quotationline", "planning.weddingproject",
                 "payments.invoice", "payments.paymentrecord", "payments.refundrecord", "payments.commissionrecord",
                 "payments.vendorsettlementrecord"],
        "change": ["payments.invoice", "payments.paymentrecord", "payments.refundrecord",
                  "payments.vendorsettlementrecord", "payments.commissionrecord"],
        "add": ["payments.invoice", "payments.paymentrecord", "payments.refundrecord"],
        "custom": ["payments.verify_payment", "payments.approve_refund", "payments.manage_settlement"],
    },
}


def _perm(label: str, action: str | None):
    app, model = label.split(".")
    codename = f"{action}_{model}" if action else model
    return Permission.objects.filter(content_type__app_label=app, codename=codename).first()


def ensure_roles(sender=None, **kwargs):
    from django.apps import apps
    from django.contrib.auth.management import create_permissions

    # Make sure every app's permissions exist before we attach them (post_migrate order is not guaranteed).
    for app_config in apps.get_app_configs():
        create_permissions(app_config, verbosity=0)
    for group_name, spec in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=group_name)
        perms = []
        for action in ("view", "change", "add"):
            for label in spec.get(action, []) + (spec.get("view_extra", []) if action == "view" else []):
                perms.append(_perm(label, action))
        for label in spec.get("custom", []):
            perms.append(_perm(label, None))
        group.permissions.set([p for p in perms if p])
