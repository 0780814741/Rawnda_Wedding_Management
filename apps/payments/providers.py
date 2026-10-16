"""
Payment-provider abstraction.

IMPORTANT: no real provider is integrated. `ManualProvider` is the only working implementation:
staff record payments received outside the system and reconcile them. Adding a real provider means:
  1. Confirm the provider is licensed/permitted for marketplace collections and vendor settlements in
     Rwanda and agree the fund-flow with them (regulatory confirmation is OUTSIDE this codebase).
  2. Implement `PaymentProvider` against that provider's documented API (do not guess endpoints).
  3. Verify webhook signatures with the provider's scheme (helper below) and use `record_provider_event`
     for idempotency. Never mark an invoice paid from a browser redirect.
"""
import hashlib
import hmac
from abc import ABC, abstractmethod
from dataclasses import dataclass

from django.conf import settings
from django.db import IntegrityError, transaction

from .models import ProviderEvent


class ProviderNotConfigured(Exception):
    pass


@dataclass
class PaymentInstructions:
    text: str
    pay_url: str = ""  # only ever set by a real provider integration


class PaymentProvider(ABC):
    name = "abstract"

    @abstractmethod
    def get_instructions(self, invoice) -> PaymentInstructions: ...

    @abstractmethod
    def verify_notification(self, body: bytes, signature: str) -> bool: ...


class ManualProvider(PaymentProvider):
    name = "manual"

    def get_instructions(self, invoice):
        return PaymentInstructions(text=invoice.payment_instructions or settings.PAYMENT_INSTRUCTIONS)

    def verify_notification(self, body, signature):
        raise ProviderNotConfigured("Manual payments have no automatic notifications.")


def get_provider() -> PaymentProvider:
    if settings.PAYMENT_PROVIDER == "manual":
        return ManualProvider()
    raise ProviderNotConfigured(f"Payment provider '{settings.PAYMENT_PROVIDER}' is not implemented.")


def verify_hmac_sha256(secret: str, body: bytes, signature_hex: str) -> bool:
    """Constant-time HMAC-SHA256 check. Use only if the provider documents this exact scheme."""
    if not secret or not signature_hex:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_hex.strip().lower())


def record_provider_event(provider: str, event_id: str, signature_valid: bool) -> bool:
    """Return True if this event is new and valid (process it); False if duplicate or invalid (ignore)."""
    if not signature_valid:
        return False
    try:
        with transaction.atomic():  # savepoint, so a duplicate does not poison an outer transaction
            ProviderEvent.objects.create(provider=provider, event_id=event_id, signature_valid=True)
    except IntegrityError:
        return False
    return True
