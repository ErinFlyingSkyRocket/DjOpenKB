from django.db import migrations, models


def set_existing_auth_sources(apps, schema_editor):
    UserProfile = apps.get_model("kb", "UserProfile")
    UserProfile.objects.filter(account_type__in=["ldap_user", "ldap_admin"]).update(auth_source="ad")
    UserProfile.objects.exclude(account_type__in=["ldap_user", "ldap_admin"]).update(auth_source="local")


def reverse_existing_auth_sources(apps, schema_editor):
    # No-op. The field is removed by the reverse schema operation.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0007_article_image_upload_log"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="auth_source",
            field=models.CharField(
                choices=[("local", "Local user"), ("ad", "Active Directory user")],
                default="local",
                help_text="Controls whether the password is managed locally in DjOpenKB or externally by Active Directory.",
                max_length=20,
            ),
        ),
        migrations.RunPython(set_existing_auth_sources, reverse_existing_auth_sources),
    ]
