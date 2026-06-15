# Generated for DjOpenKB authentication lockout policy stages.

from django.db import migrations, models
import django.db.models.deletion


DEFAULT_LOCKOUT_STAGES = [
    # sort_order, failure_limit, failure_window_seconds, block_seconds, repeat_count
    (10, 10, 600, 300, 2),   # 10 failures within 10 minutes -> 5 minutes, twice
    (20, 5, 600, 900, 2),    # 5 failures within 10 minutes -> 15 minutes, twice
    (30, 3, 600, 3600, 0),   # 3 failures within 10 minutes -> 1 hour, repeat forever
]


def create_default_lockout_policy(apps, schema_editor):
    SiteSetting = apps.get_model("kb", "SiteSetting")
    AuthLockoutPolicyStage = apps.get_model("kb", "AuthLockoutPolicyStage")

    setting, _created = SiteSetting.objects.get_or_create(pk=1)
    if AuthLockoutPolicyStage.objects.filter(site_setting=setting).exists():
        return

    AuthLockoutPolicyStage.objects.bulk_create(
        [
            AuthLockoutPolicyStage(
                site_setting=setting,
                sort_order=sort_order,
                failure_limit=failure_limit,
                failure_window_seconds=failure_window_seconds,
                block_seconds=block_seconds,
                repeat_count=repeat_count,
                enabled=True,
            )
            for sort_order, failure_limit, failure_window_seconds, block_seconds, repeat_count in DEFAULT_LOCKOUT_STAGES
        ]
    )


def remove_default_lockout_policy(apps, schema_editor):
    # Keep admin-edited policy rows on migration rollback rather than deleting
    # possible local changes. The model/table will be removed by the reverse
    # CreateModel operation if the migration is actually unapplied.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0025_articles_per_page_setting"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="auth_lockout_strike_ttl_seconds",
            field=models.PositiveIntegerField(
                default=604800,
                help_text=(
                    "How long failed-login/MFA lockout history is remembered if the user never signs in successfully. "
                    "Successful password/MFA verification resets the relevant lockout history immediately. Default is 604800 seconds (7 days)."
                ),
                verbose_name="Authentication lockout escalation memory (seconds)",
            ),
        ),
        migrations.AlterField(
            model_name="authactivitylog",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("password_success", "Password login success"),
                    ("password_failure", "Password login failure"),
                    ("pending_mfa", "Pending MFA created"),
                    ("mfa_setup_success", "MFA setup success"),
                    ("mfa_setup_failure", "MFA setup failure"),
                    ("mfa_verify_success", "MFA verify success"),
                    ("mfa_verify_failure", "MFA verify failure"),
                    ("mfa_reset_self", "MFA reset by user"),
                    ("mfa_reset_admin", "MFA reset by admin"),
                    ("auth_lockout_reset_admin", "Authentication lockout reset by admin"),
                    ("logout", "Logout"),
                ],
                db_index=True,
                max_length=40,
            ),
        ),
        migrations.CreateModel(
            name="AuthLockoutPolicyStage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "sort_order",
                    models.PositiveIntegerField(
                        default=10,
                        help_text="Lower numbers run first. Use 10, 20, 30, etc. so you can insert stages later.",
                        verbose_name="Stage order",
                    ),
                ),
                (
                    "failure_limit",
                    models.PositiveIntegerField(
                        default=10,
                        help_text="Number of wrong password/MFA attempts required before this stage blocks the user.",
                        verbose_name="Failed attempts before block",
                    ),
                ),
                (
                    "failure_window_seconds",
                    models.PositiveIntegerField(
                        default=600,
                        help_text="Failures must happen within this time window to trigger the stage. Default is 600 seconds (10 minutes).",
                        verbose_name="Failure counting window (seconds)",
                    ),
                ),
                (
                    "block_seconds",
                    models.PositiveIntegerField(
                        default=300,
                        help_text="How long the login/MFA check is blocked after this stage triggers.",
                        verbose_name="Block duration (seconds)",
                    ),
                ),
                (
                    "repeat_count",
                    models.PositiveIntegerField(
                        default=1,
                        help_text="How many lockouts should use this stage before moving to the next stage. Use 0 on the final stage to repeat forever.",
                        verbose_name="Repeat count",
                    ),
                ),
                ("enabled", models.BooleanField(default=True, verbose_name="Enabled")),
                (
                    "site_setting",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="auth_lockout_stages",
                        to="kb.sitesetting",
                    ),
                ),
            ],
            options={
                "verbose_name": "Authentication lockout policy stage",
                "verbose_name_plural": "Authentication lockout policy stages",
                "ordering": ["sort_order", "id"],
            },
        ),
        migrations.RunPython(create_default_lockout_policy, remove_default_lockout_policy),
    ]
