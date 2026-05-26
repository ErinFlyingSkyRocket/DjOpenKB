# Generated for DjOpenKB authentication/MFA monitoring.

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0002_usermfadevice"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuthActivityLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(db_index=True, default=timezone.now)),
                ("event_type", models.CharField(choices=[("password_success", "Password login success"), ("password_failure", "Password login failure"), ("pending_mfa", "Pending MFA created"), ("mfa_setup_success", "MFA setup success"), ("mfa_setup_failure", "MFA setup failure"), ("mfa_verify_success", "MFA verify success"), ("mfa_verify_failure", "MFA verify failure"), ("mfa_reset_self", "MFA reset by user"), ("mfa_reset_admin", "MFA reset by admin"), ("logout", "Logout")], db_index=True, max_length=40)),
                ("success", models.BooleanField(db_index=True, default=False)),
                ("username", models.CharField(blank=True, db_index=True, max_length=255)),
                ("login_mode", models.CharField(blank=True, db_index=True, max_length=30)),
                ("ip_address", models.GenericIPAddressField(blank=True, db_index=True, null=True)),
                ("user_agent", models.TextField(blank=True)),
                ("path", models.CharField(blank=True, max_length=500)),
                ("request_method", models.CharField(blank=True, max_length=10)),
                ("details", models.JSONField(blank=True, default=dict)),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="auth_activity_logs", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Authentication activity log",
                "verbose_name_plural": "Authentication activity logs",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="authactivitylog",
            index=models.Index(fields=["-created_at", "event_type"], name="kb_authacti_created_9c7968_idx"),
        ),
        migrations.AddIndex(
            model_name="authactivitylog",
            index=models.Index(fields=["ip_address", "-created_at"], name="kb_authacti_ip_addr_f1d0bb_idx"),
        ),
        migrations.AddIndex(
            model_name="authactivitylog",
            index=models.Index(fields=["username", "-created_at"], name="kb_authacti_usernam_0b8fd5_idx"),
        ),
    ]
