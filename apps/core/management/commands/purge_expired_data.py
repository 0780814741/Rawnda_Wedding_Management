"""Retention job: expire old quotations, anonymise deletion-requested accounts, drop stale draft plans."""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import CustomerProfile
from apps.core.audit import log_action
from apps.operations.services import expire_quotations
from apps.planning.models import WeddingProject


class Command(BaseCommand):
    help = "Run regularly (e.g. a Render cron job). Use --dry-run to preview."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **opts):
        dry = opts["dry_run"]
        cutoff = timezone.now() - timedelta(days=settings.DATA_RETENTION_DAYS)
        # 1. stale drafts that never reached our team
        stale = WeddingProject.objects.filter(status=WeddingProject.Status.DRAFT, updated_at__lt=cutoff)
        self.stdout.write(f"stale draft plans: {stale.count()}")
        # 2. accounts that asked for deletion: anonymise, keep financial rows (PROTECT) without personal data
        profiles = CustomerProfile.objects.filter(deletion_requested_at__isnull=False, user__is_active=True, user__is_staff=False)
        self.stdout.write(f"accounts to anonymise: {profiles.count()}")
        if dry:
            self.stdout.write("dry run: nothing changed")
            return
        n_quotes = expire_quotations()
        stale.delete()
        for prof in profiles.select_related("user"):
            u = prof.user
            u.email, u.first_name, u.last_name, u.is_active = f"deleted-{u.pk}@invalid.local", "", "", False
            u.set_unusable_password()
            u.save()
            prof.phone = ""
            prof.save(update_fields=["phone", "updated_at"])
            u.wedding_projects.update(preferences="", title="Deleted wedding")
            u.notifications.all().delete()
            log_action(None, "account_anonymised", u)
        self.stdout.write(self.style.SUCCESS(f"expired quotations: {n_quotes}; anonymised accounts done."))
