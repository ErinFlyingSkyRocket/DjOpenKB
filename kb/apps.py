from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class KbConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "kb"
    verbose_name = _("KB")

    def ready(self):
        import kb.signals  # noqa: F401
