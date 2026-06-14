from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from kb.mfa import get_totp_valid_window, mfa_device_secret_is_readable, verify_totp_code
from kb.models import UserMFADevice


class Command(BaseCommand):
    help = (
        "Diagnose DjOpenKB MFA without printing TOTP secrets. "
        "Use this when users keep seeing 'wrong code'."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "username",
            nargs="?",
            help="Optional username or email to diagnose one user instead of all MFA devices.",
        )
        parser.add_argument(
            "--code",
            default="",
            help="Optional current 6-digit code from the user's authenticator. The code is only verified, never printed.",
        )

    def _find_user(self, username_or_email):
        UserModel = get_user_model()
        try:
            return UserModel.objects.get(
                Q(username__iexact=username_or_email) | Q(email__iexact=username_or_email)
            )
        except UserModel.DoesNotExist as exc:
            raise CommandError(f"No user found for: {username_or_email}") from exc
        except UserModel.MultipleObjectsReturned as exc:
            raise CommandError(f"Multiple users matched: {username_or_email}. Use the exact username.") from exc

    def _describe_device(self, user, code):
        device = getattr(user, "kb_mfa_device", None)
        if device is None:
            return {
                "username": user.get_username(),
                "email": user.email or "-",
                "active": user.is_active,
                "has_device": False,
                "confirmed": False,
                "encrypted": False,
                "secret_readable": False,
                "code_ok": None,
            }

        secret_readable = mfa_device_secret_is_readable(device)
        return {
            "username": user.get_username(),
            "email": user.email or "-",
            "active": user.is_active,
            "has_device": True,
            "confirmed": device.confirmed,
            "encrypted": device.secret_is_encrypted,
            "secret_readable": secret_readable,
            "code_ok": verify_totp_code(device, code) if code else None,
        }

    def handle(self, *args, **options):
        username = options.get("username")
        code = (options.get("code") or "").strip()

        self.stdout.write(f"Server time now: {timezone.now():%Y-%m-%d %H:%M:%S %Z}")
        self.stdout.write(f"MFA_TOTP_VALID_WINDOW: {get_totp_valid_window()} 30-second window(s)")
        self.stdout.write(
            "If all users fail MFA, compare this server time with the phone/authenticator time first."
        )
        self.stdout.write("")

        if username:
            users = [self._find_user(username)]
        else:
            users = [device.user for device in UserMFADevice.objects.select_related("user").order_by("user__username")]

        if not users:
            self.stdout.write(self.style.WARNING("No MFA devices found."))
            return

        unreadable_count = 0
        for user in users:
            row = self._describe_device(user, code)
            if row["has_device"] and not row["secret_readable"]:
                unreadable_count += 1

            code_text = "not tested"
            if row["code_ok"] is True:
                code_text = "PASS"
            elif row["code_ok"] is False:
                code_text = "FAIL"

            self.stdout.write(
                f"{row['username']} | email={row['email']} | active={row['active']} | "
                f"device={row['has_device']} | confirmed={row['confirmed']} | "
                f"encrypted={row['encrypted']} | secret_readable={row['secret_readable']} | code={code_text}"
            )

        if unreadable_count:
            self.stdout.write("")
            self.stdout.write(
                self.style.ERROR(
                    f"{unreadable_count} MFA device(s) have unreadable secrets. "
                    "This usually means DJANGO_FIELD_ENCRYPTION_KEY or DJANGO_SECRET_KEY changed after MFA setup. "
                    "Reset MFA for affected users and keep the encryption key stable."
                )
            )
