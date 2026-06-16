# Generated to change Django Admin MFA idle-timeout default from 30 minutes to 10 minutes.

from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


def set_existing_default_timeout_to_10_minutes(apps, schema_editor):
    SiteSetting = apps.get_model("kb", "SiteSetting")
    # Preserve custom administrator values. Only change rows that still use the old default.
    SiteSetting.objects.filter(admin_mfa_idle_timeout_seconds=1800).update(
        admin_mfa_idle_timeout_seconds=600
    )


def reverse_existing_default_timeout_to_30_minutes(apps, schema_editor):
    SiteSetting = apps.get_model("kb", "SiteSetting")
    # Conservative reverse for rollback.
    SiteSetting.objects.filter(admin_mfa_idle_timeout_seconds=600).update(
        admin_mfa_idle_timeout_seconds=1800
    )


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0009_admin_mfa_idle_timeout_site_setting"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sitesetting",
            name="admin_mfa_idle_timeout_seconds",
            field=models.PositiveIntegerField(
                default=600,
                verbose_name=_("Admin MFA idle timeout (seconds)"),
                help_text=_(
                    "How long an administrator may stay inactive inside Django Admin after completing the extra admin MFA check. "
                    "Default is 600 seconds (10 minutes). Minimum enforced by code is 60 seconds; maximum enforced by code is 86400 seconds."
                ),
            ),
        ),
        migrations.RunPython(
            set_existing_default_timeout_to_10_minutes,
            reverse_existing_default_timeout_to_30_minutes,
        ),
    ]
