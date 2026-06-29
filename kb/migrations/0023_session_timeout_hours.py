# Generated for public-facing eight-hour fixed session expiry.

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


def set_existing_session_timeout_to_eight_hours(apps, schema_editor):
    """Replace the former days-based default with the hardened 8-hour policy."""
    SiteSetting = apps.get_model("kb", "SiteSetting")
    for setting in SiteSetting.objects.all():
        setting.session_timeout_hours = 8
        setting.save(update_fields=["session_timeout_hours"])


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0022_admin_mfa_event_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="session_timeout_hours",
            field=models.PositiveIntegerField(
                default=8,
                help_text=_(
                    "Authenticated and pending-MFA sessions expire after this many hours from sign-in. "
                    "Default is 8 hours. Allowed range: 1 to 168 hours (7 days)."
                ),
                validators=[MinValueValidator(1), MaxValueValidator(168)],
                verbose_name=_("User session timeout (hours)"),
            ),
        ),
        migrations.RunPython(
            set_existing_session_timeout_to_eight_hours,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name="sitesetting",
            name="session_timeout_days",
        ),
    ]
