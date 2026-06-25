# Generated for explicit authentication-lockout audit events.

from django.db import migrations, models
from django.utils.translation import gettext_lazy as _


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0020_manager_role_precedence"),
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
                    ("mfa_reset_self", _("MFA reset by user")),
                    ("mfa_reset_admin", _("MFA reset by admin")),
                    ("auth_lockout_triggered", _("Authentication lockout triggered")),
                    ("auth_lockout_reset_admin", _("Authentication lockout reset by admin")),
                    ("logout", _("Logout")),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
    ]
