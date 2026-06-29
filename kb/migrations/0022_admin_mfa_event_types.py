# Generated for distinct Django Admin MFA audit event labels.

from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0021_auth_lockout_triggered_event"),
    ]

    operations = [
        migrations.AlterField(
            model_name="authactivitylog",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("password_success", _("Password login success")),
                    ("password_failure", _("Password login failure")),
                    ("pending_mfa", _("Pending MFA created")),
                    ("mfa_setup_success", _("MFA setup success")),
                    ("mfa_setup_failure", _("MFA setup failure")),
                    ("mfa_verify_success", _("MFA verify success")),
                    ("mfa_verify_failure", _("MFA verify failure")),
                    ("admin_mfa_verify_success", _("Django Admin MFA verification success")),
                    ("admin_mfa_verify_failure", _("Django Admin MFA verification failure")),
                    ("mfa_reset_self", _("MFA reset by user")),
                    ("mfa_reset_admin", _("MFA reset by admin")),
                    ("auth_lockout_triggered", _("Authentication lockout triggered")),
                    ("admin_mfa_lockout_triggered", _("Django Admin MFA lockout triggered")),
                    ("auth_lockout_reset_admin", _("Authentication lockout reset by admin")),
                    ("logout", _("Logout")),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
    ]
