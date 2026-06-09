from django.db import migrations, models


def encrypt_existing_mfa_secrets(apps, schema_editor):
    # Import the project helper here so the migration uses the same encryption
    # format as the application code.
    from kb.crypto import encrypt_value, is_encrypted_value

    UserMFADevice = apps.get_model("kb", "UserMFADevice")
    for device in UserMFADevice.objects.all():
        secret = device.secret or ""
        if secret and not is_encrypted_value(secret):
            device.secret = encrypt_value(secret)
            device.save(update_fields=["secret"])


def noop_reverse(apps, schema_editor):
    # Do not decrypt secrets during rollback. Keeping them encrypted is safer.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0011_admin_log_rows_per_page"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usermfadevice",
            name="secret",
            field=models.CharField(max_length=512),
        ),
        migrations.RunPython(encrypt_existing_mfa_secrets, noop_reverse),
    ]
