from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from kb.mfa import admin_reset_user_mfa


class Command(BaseCommand):
    help = "Reset MFA for one DjOpenKB user from the server command line."

    def add_arguments(self, parser):
        parser.add_argument("username", help="Username or email address of the user whose MFA should be reset.")
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Confirm the reset without an interactive prompt.",
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

    def handle(self, *args, **options):
        user = self._find_user(options["username"])

        if not options["yes"]:
            answer = input(
                f"Reset MFA for {user.get_username()}? Existing sessions will be cleared. Type yes to continue: "
            ).strip().lower()
            if answer != "yes":
                self.stdout.write(self.style.WARNING("MFA reset cancelled."))
                return

        _device, sessions_deleted = admin_reset_user_mfa(user)
        self.stdout.write(
            self.style.SUCCESS(
                f"MFA reset for {user.get_username()}. Cleared {sessions_deleted} session(s). "
                "The user must sign in again and scan a new authenticator QR code."
            )
        )
