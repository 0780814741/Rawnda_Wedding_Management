from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AccountsConfig(AppConfig):
    name = "apps.accounts"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from .roles import ensure_roles

        post_migrate.connect(ensure_roles, sender=self)
