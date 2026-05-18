from django.apps import AppConfig


class KbConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "kb"

    def ready(self):
        import kb.signals  # noqa: F401
