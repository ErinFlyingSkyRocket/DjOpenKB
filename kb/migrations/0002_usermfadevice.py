# Generated for DjOpenKB local-account MFA.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="UserMFADevice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("secret", models.CharField(max_length=64)),
                ("confirmed", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("last_verified_at", models.DateTimeField(blank=True, null=True)),
                ("reset_at", models.DateTimeField(blank=True, null=True)),
                (
                    "user",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="kb_mfa_device",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "User MFA device",
                "verbose_name_plural": "User MFA devices",
            },
        ),
    ]
