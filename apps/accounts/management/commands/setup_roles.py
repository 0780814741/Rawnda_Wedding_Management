from django.core.management.base import BaseCommand

from apps.accounts.roles import ensure_roles


class Command(BaseCommand):
    help = "Create/refresh staff role groups (Planner, Operations Manager, Finance). Safe to run repeatedly."

    def handle(self, *args, **options):
        ensure_roles()
        self.stdout.write(self.style.SUCCESS("Roles are up to date."))
