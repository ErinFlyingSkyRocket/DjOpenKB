# Generated for configurable Django Admin MFA idle timeout.

from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0008_disabled_user_highest_precedence"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="admin_mfa_idle_timeout_seconds",
            field=models.PositiveIntegerField(
                default=1800,
                verbose_name=_("Admin MFA idle timeout (seconds)"),
                help_text=_(
                    "How long an administrator may stay inactive inside Django Admin after completing the extra admin MFA check. "
                    "Default is 1800 seconds (30 minutes). Minimum enforced by code is 60 seconds; maximum enforced by code is 86400 seconds."
                ),
            ),
        ),
    ]
