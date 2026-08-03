# Generated for the configurable Django Admin MFA verification deadline.

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0004_sitesetting_mfa_login_timeout_seconds"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="admin_mfa_verification_timeout_seconds",
            field=models.PositiveIntegerField(
                default=60,
                help_text=(
                    "Maximum time allowed to complete the separate MFA check before entering Django Admin. "
                    "When the countdown reaches zero, the administrator stays signed in and must start a new "
                    "verification window before trying again. Default is 60 seconds. Allowed range: 30 to 900 "
                    "seconds (15 minutes)."
                ),
                validators=[MinValueValidator(30), MaxValueValidator(900)],
                verbose_name="Admin MFA verification timeout (seconds)",
            ),
        ),
    ]
