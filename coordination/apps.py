from django.apps import AppConfig


class CoordinationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "coordination"
    verbose_name = "Workforce coordination"

    def ready(self):
        from django.db.models.signals import post_migrate
        from .signals import seed_service_catalog
        post_migrate.connect(seed_service_catalog, sender=self, dispatch_uid="coordination.seed_service_catalog")
