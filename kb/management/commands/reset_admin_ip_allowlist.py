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
