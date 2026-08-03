"""Emergency recovery command for the dynamic Django Admin IP allowlist.

Run from the Ubuntu server host:
    cd /opt/DjOpenKB
    sudo docker compose exec web \
      python manage.py reset_admin_ip_allowlist

Show command help:
    sudo docker compose exec web \
      python manage.py reset_admin_ip_allowlist --help

Purpose and security warning:
    Disables the dynamic Admin IP allowlist and permanently clears all stored IP
    addresses and CIDR ranges. Use this only when authorised administrators are
    locked out by an incorrect allowlist. Normal login, superuser permission,
    and Admin MFA remain required after the reset.
"""

from django.core.management.base import BaseCommand

from kb.models import SiteSetting


class Command(BaseCommand):
    help = (
        "Emergency recovery command that fully resets the dynamic Django Admin "
        "IP allowlist by disabling it and clearing all configured IPv4/IPv6 "
        "addresses and CIDR ranges."
    )

    def handle(self, *args, **options):
        site_setting = SiteSetting.load()

        already_reset = (
            not site_setting.admin_ip_allowlist_enabled
            and not (site_setting.admin_allowed_cidrs or "").strip()
        )

        if already_reset:
            self.stdout.write(
                self.style.WARNING(
                    "The Admin IP allowlist is already fully reset. "
                    "The allowlist is disabled and no IP/CIDR ranges are stored."
                )
            )
            return

        site_setting.admin_ip_allowlist_enabled = False
        site_setting.admin_allowed_cidrs = ""
        site_setting.save(
            update_fields=[
                "admin_ip_allowlist_enabled",
                "admin_allowed_cidrs",
                "updated_at",
            ]
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Admin IP allowlist reset successfully. "
                "The allowlist is disabled and all configured IPv4/IPv6 "
                "addresses and CIDR ranges have been cleared. "
                "Admin access is now unrestricted by source IP, but normal login, "
                "superuser permissions, and Admin MFA are still required."
            )
        )
