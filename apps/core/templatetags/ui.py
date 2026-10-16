from django import template

register = template.Library()


@register.filter
def rwf(value):
    if value is None or value == "":
        return "—"
    try:
        return f"RWF {int(value):,}"
    except (TypeError, ValueError):
        return "—"


@register.filter
def abs_val(value):
    try:
        return abs(int(value))
    except (TypeError, ValueError):
        return value


_LABELS = {"mc": "MC", "dj": "DJ", "sound-lighting": "Sound and lighting", "traditional-performers": "Traditional performers",
           "bridal-wear": "Bridal wear", "gusaba": "Gusaba", "traditional": "Traditional wedding", "civil": "Civil ceremony",
           "church": "Church ceremony", "reception": "Reception"}


@register.filter
def label_of(value):
    value = str(value)
    return _LABELS.get(value) or value.replace("-", " ").replace("_", " ").capitalize()


PRICE_LABELS = {
    "indicative": ("Indicative price", "badge-warn", "Not yet confirmed by the vendor. Our team will confirm it before you pay anything."),
    "vendor_quoted": ("Vendor-confirmed quote", "badge-ok", "The vendor confirmed this price to our team."),
    "booked": ("Confirmed booking", "badge-ok", "The vendor confirmed this booking."),
}


@register.inclusion_tag("partials/price_badge.html")
def price_badge(status, outdated=False):
    label, css, help_text = PRICE_LABELS.get(status, ("Unknown", "badge-warn", ""))
    return {"label": label, "css": css, "help": help_text, "outdated": outdated}
