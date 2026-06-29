"""SMTP backend for the internal AD relay.

Django's standard SMTP backend validates certificates using the operating system
trust store. This subclass additionally supports a private AD/enterprise CA file
mounted into the container, while preserving normal hostname and certificate
validation for both STARTTLS and implicit TLS.
"""

from __future__ import annotations

import ssl

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend as DjangoSMTPEmailBackend
from django.utils.functional import cached_property


class ADRelayEmailBackend(DjangoSMTPEmailBackend):
    """Django SMTP backend with optional strict trust of a private relay CA."""

    @cached_property
    def ssl_context(self):
        # Start with the OS trust store, then add the enterprise/AD CA when one
        # is configured. This preserves hostname/certificate verification while
        # supporting a private relay certificate chain.
        context = ssl.create_default_context()
        ca_cert_file = (getattr(settings, "SMTP_RELAY_CA_CERT_FILE", "") or "").strip()
        if ca_cert_file:
            context.load_verify_locations(cafile=ca_cert_file)
        return context
