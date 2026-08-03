# Generated for the configurable password-to-MFA login completion deadline.

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0003_remove_articledailyview_use_session_views"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitesetting",
            name="session_timeout_hours",
            field=models.PositiveIntegerField(
                default=8,
                help_text=(
                    "Authenticated sessions expire after this many hours from sign-in. Pending-MFA sessions "
                    "cannot exceed this lifetime, but they normally expire sooner according to the separate "
                    "MFA login completion timeout. Default is 8 hours. Allowed range: 1 to 168 hours (7 days)."
                ),
                validators=[MinValueValidator(1), MaxValueValidator(168)],
                verbose_name="User session timeout (hours)",
            ),
        ),
        migrations.AddField(
            model_name="sitesetting",
            name="mfa_login_timeout_seconds",
            field=models.PositiveIntegerField(
                default=60,
                help_text=(
                    "Maximum time allowed to complete MFA after the username/password step succeeds. "
                    "When the countdown reaches zero, the pending login is cleared and the user must enter "
                    "their username and password again. Default is 60 seconds. Allowed range: 30 to 900 "
                    "seconds (15 minutes)."
                ),
                validators=[MinValueValidator(30), MaxValueValidator(900)],
                verbose_name="MFA login completion timeout (seconds)",
            ),
        ),
    ]
