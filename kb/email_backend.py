"""SMTP backend with optional private Exchange trust certificate support.

Django's standard SMTP backend validates certificates against the operating
system trust store. This subclass can additionally load one mounted public
trust certificate for an internal Exchange relay, while preserving normal
certificate-chain and hostname validation for STARTTLS and implicit TLS.
"""

from __future__ import annotations

import ssl

from django.conf import settings
from django.core.mail.backends.smtp import EmailBackend as DjangoSMTPEmailBackend
from django.utils.functional import cached_property


class TrustedRelayEmailBackend(DjangoSMTPEmailBackend):
    """Django SMTP backend with an optional, strictly validated trust anchor."""

    @cached_property
    def ssl_context(self):
        # Start with the operating-system trust store. When an internal relay
        # uses a private CA or a self-signed server certificate, add the public
        # certificate configured by SMTP_RELAY_CA_CERT_FILE as an additional
        # trust anchor. ssl.create_default_context() keeps hostname checking and
        # certificate verification enabled.
        context = ssl.create_default_context()
        trust_cert_file = (
            getattr(settings, "SMTP_RELAY_CA_CERT_FILE", "") or ""
        ).strip()
        if trust_cert_file:
            context.load_verify_locations(cafile=trust_cert_file)
        return context
