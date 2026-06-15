# Generated for DjOpenKB authentication lockout policy simplification.

from django.db import migrations


OLD_DEFAULT_STAGES = [
    (10, 10, 600, 300, 2),
    (20, 5, 600, 900, 2),
    (30, 3, 600, 1800, 1),
    (40, 3, 600, 3600, 1),
    (50, 3, 600, 7200, 1),
    (60, 3, 600, 86400, 0),
]

NEW_DEFAULT_STAGES = [
    (10, 10, 600, 300, 2),
    (20, 5, 600, 900, 2),
    (30, 3, 600, 3600, 0),
]


def _stage_tuple(row):
    return (row.sort_order, row.failure_limit, row.failure_window_seconds, row.block_seconds, row.repeat_count)


def simplify_default_lockout_policy(apps, schema_editor):
    SiteSetting = apps.get_model("kb", "SiteSetting")
    AuthLockoutPolicyStage = apps.get_model("kb", "AuthLockoutPolicyStage")

    setting, _created = SiteSetting.objects.get_or_create(pk=1)
    existing = list(setting.auth_lockout_stages.order_by("sort_order", "id"))
    existing_tuples = [_stage_tuple(row) for row in existing]

    # Do not overwrite a policy the admin has already customized.
    if existing and existing_tuples != OLD_DEFAULT_STAGES:
        return

    AuthLockoutPolicyStage.objects.filter(site_setting=setting).delete()
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
            for sort_order, failure_limit, failure_window_seconds, block_seconds, repeat_count in NEW_DEFAULT_STAGES
        ]
    )


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("kb", "0026_auth_lockout_policy_stages"),
    ]

    operations = [
        migrations.RunPython(simplify_default_lockout_policy, reverse_noop),
    ]
