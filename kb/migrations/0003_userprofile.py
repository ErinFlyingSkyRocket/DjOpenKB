# Generated for DjOpenKB account type separation.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def create_profiles_for_existing_users(apps, schema_editor):
    User = apps.get_model("auth", "User")
    UserProfile = apps.get_model("kb", "UserProfile")

    for user in User.objects.all():
        if user.is_superuser or user.is_staff:
            account_type = "admin"
        else:
            account_type = "user"

        UserProfile.objects.get_or_create(
            user=user,
            defaults={
                "account_type": account_type,
                "can_access_main_site": True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("kb", "0002_suggestedarticle_image_assets"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("account_type", models.CharField(choices=[("admin", "Admin"), ("user", "User"), ("ldap_user", "LDAP user"), ("ldap_admin", "LDAP admin")], default="user", help_text="Admin/LDAP admin accounts can access Django admin when staff status is enabled.", max_length=20)),
                ("can_access_main_site", models.BooleanField(default=True, help_text="Untick this to block the user from accessing the main wiki site.")),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="kb_profile", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Main Site User Profile",
                "verbose_name_plural": "Main Site User Profiles",
            },
        ),
        migrations.RunPython(create_profiles_for_existing_users, migrations.RunPython.noop),
    ]
