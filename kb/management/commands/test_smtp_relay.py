"""Send one operator-selected SMTP relay test message without exposing secrets."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.core.management.base import BaseCommand, CommandError

from kb.notifications import _allowed_recipient_domains


class Command(BaseCommand):
    help = (
        "Send a single SMTP relay test message to a supplied address. "
        "Requires EMAIL_NOTIFICATIONS_ENABLED=true."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "recipient",
            help="Mailbox that is approved to receive the SMTP relay test message.",
        )

    def handle(self, *args, **options):
        if not getattr(settings, "EMAIL_NOTIFICATIONS_ENABLED", False):
            raise CommandError(
                "Email notifications are disabled. Configure the relay and set "
                "EMAIL_NOTIFICATIONS_ENABLED=true before sending a test."
            )

        recipient = (options["recipient"] or "").strip()
        try:
            validate_email(recipient)
        except ValidationError as exc:
            raise CommandError("Recipient must be a valid email address.") from exc

        email_domain = recipient.rpartition("@")[2].casefold()
        if email_domain not in _allowed_recipient_domains():
            raise CommandError(
                "Recipient domain is not in SMTP_RELAY_ALLOWED_RECIPIENT_DOMAINS."
            )

        message = EmailMessage(
            subject=f"{settings.EMAIL_SUBJECT_PREFIX}SMTP relay test",
            body=(
                "This is a test message from Knowledge Repository.\n\n"
                "The SMTP relay configuration, TLS validation, and service-account "
                "authentication were accepted by Django."
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
        )

        try:
            delivered = message.send(fail_silently=False)
        except Exception as exc:
            # Do not print configuration, credentials, or full relay debugging data.
            raise CommandError(
                f"SMTP relay test failed ({type(exc).__name__}). "
                "Check the web service logs and the relay configuration."
            ) from exc

        if delivered != 1:
            raise CommandError(
                "SMTP relay did not report delivery of the test message. "
                "Check the web service logs and the relay configuration."
            )

        self.stdout.write(
            self.style.SUCCESS(
                "SMTP relay accepted one test message. Check the recipient mailbox."
            )
        )
