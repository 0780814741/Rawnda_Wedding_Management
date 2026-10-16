import json
import logging

from django.http import HttpResponse, HttpResponseNotAllowed
from django.views.decorators.csrf import csrf_exempt

from .providers import ProviderNotConfigured, get_provider, record_provider_event

logger = logging.getLogger("payments")


@csrf_exempt
def provider_webhook(request, provider):
    """Placeholder endpoint for a FUTURE provider. Disabled (501) until a real provider is implemented.

    When enabled it must: verify the provider signature on the raw body, record the event id for
    idempotency, then look the payment up server-side. A browser redirect is never proof of payment.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        prov = get_provider()
        valid = prov.verify_notification(request.body, request.headers.get("X-Signature", ""))
    except ProviderNotConfigured:
        return HttpResponse("Payment provider notifications are not enabled.", status=501)
    try:
        event_id = str(json.loads(request.body).get("id", ""))
    except (ValueError, AttributeError):
        return HttpResponse(status=400)
    if not valid:
        logger.warning("webhook rejected: invalid signature provider=%s", provider)
        return HttpResponse(status=400)
    if not event_id or not record_provider_event(prov.name, event_id, valid):
        return HttpResponse(status=200)  # duplicate: acknowledge without reprocessing
    # A real integration would now fetch/confirm the payment with the provider and record it.
    return HttpResponse(status=202)
