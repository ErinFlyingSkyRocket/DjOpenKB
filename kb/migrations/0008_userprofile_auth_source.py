from django.db import migrations, models


def populate_auth_source(apps, schema_editor):
    UserProfile = apps.get_model("kb", "UserProfile")
    UserProfile.objects.filter(account_type__in=["ldap_user", "ldap_admin"]).update(auth_source="ad")
    UserProfile.objects.exclude(account_type__in=["ldap_user", "ldap_admin"]).update(auth_source="local")


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
                db_index=True,
                default="local",
                help_text="Controls whether the user's password/email is managed locally or by Active Directory.",
                max_length=20,
            ),
        ),
        migrations.RunPython(populate_auth_source, migrations.RunPython.noop),
    ]
