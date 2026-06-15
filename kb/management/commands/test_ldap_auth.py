import getpass

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Test Knowledge Repository LDAP/AD bind, search, and optional Django authentication."

    def add_arguments(self, parser):
        parser.add_argument("username", nargs="?", help="AD username to search/test, e.g. alice or alice@openkb.local")
        parser.add_argument(
            "--bind-dn",
            default="",
            help="Temporarily test a different LDAP bind DN/UPN without editing .env, e.g. svc_djopenkb@openkb.local.",
        )
        parser.add_argument(
            "--prompt-bind-password",
            action="store_true",
            help="Prompt for the service-account password instead of using Vault/settings. Useful to verify AD credentials before patching Vault.",
        )
        parser.add_argument(
            "--auth",
            action="store_true",
            help="Also prompt for the user's AD password and test Django authenticate().",
        )

    def handle(self, *args, **options):
        try:
            import ldap
        except ImportError as exc:
            raise CommandError("python-ldap is not installed in this container.") from exc

        username = (options.get("username") or "").strip()
        self.stdout.write(f"LDAP_ENABLED: {getattr(settings, 'LDAP_ENABLED', None)}")
        self.stdout.write(f"AUTH_LDAP_SERVER_URI: {getattr(settings, 'AUTH_LDAP_SERVER_URI', None)}")
        self.stdout.write(f"AUTH_LDAP_BIND_DN: {getattr(settings, 'AUTH_LDAP_BIND_DN', None)}")
        self.stdout.write(f"Has AUTH_LDAP_BIND_PASSWORD: {bool(getattr(settings, 'AUTH_LDAP_BIND_PASSWORD', None))}")
        self.stdout.write(f"AUTHENTICATION_BACKENDS: {getattr(settings, 'AUTHENTICATION_BACKENDS', None)}")

        server = getattr(settings, "AUTH_LDAP_SERVER_URI", "")
        bind_dn = (options.get("bind_dn") or "").strip() or getattr(settings, "AUTH_LDAP_BIND_DN", "")
        bind_password = getattr(settings, "AUTH_LDAP_BIND_PASSWORD", "")
        if options.get("prompt_bind_password"):
            bind_password = getpass.getpass("LDAP service-account password: ")
        search = getattr(settings, "AUTH_LDAP_USER_SEARCH", None)

        if not server:
            raise CommandError("AUTH_LDAP_SERVER_URI is empty.")
        if not bind_dn:
            raise CommandError("AUTH_LDAP_BIND_DN is empty.")
        if not bind_password:
            raise CommandError("AUTH_LDAP_BIND_PASSWORD is empty.")
        if search is None:
            raise CommandError("AUTH_LDAP_USER_SEARCH is not configured.")

        conn = ldap.initialize(server)
        conn.set_option(ldap.OPT_REFERRALS, 0)
        network_timeout = int(getattr(settings, "LDAP_NETWORK_TIMEOUT", 5) or 5)
        operation_timeout = int(getattr(settings, "LDAP_OPERATION_TIMEOUT", 5) or 5)
        conn.set_option(ldap.OPT_NETWORK_TIMEOUT, network_timeout)
        conn.set_option(ldap.OPT_TIMEOUT, operation_timeout)

        self.stdout.write("Testing service-account bind...")
        try:
            conn.simple_bind_s(bind_dn, bind_password)
        except ldap.LDAPError as exc:
            raise CommandError(f"Service-account LDAP bind failed: {exc}") from exc
        self.stdout.write(self.style.SUCCESS("Service-account LDAP bind OK."))

        if username:
            base_dn = getattr(search, "base_dn", None) or getattr(search, "base", None)
            scope = getattr(search, "scope", ldap.SCOPE_SUBTREE)
            filterstr = getattr(search, "filterstr", None) or getattr(search, "filter", None)
            if not base_dn or not filterstr:
                raise CommandError(f"Could not inspect AUTH_LDAP_USER_SEARCH object: {search!r}")

            candidates = [username]
            if "\\" in username:
                candidates.append(username.split("\\", 1)[1])
            ad_domain = getattr(settings, "LDAP_AD_DOMAIN", "").strip().lower()
            if "@" not in username and ad_domain:
                candidates.append(f"{username}@{ad_domain}")

            seen = set()
            for candidate in candidates:
                if candidate in seen:
                    continue
                seen.add(candidate)
                ldap_filter = filterstr.replace("%(user)s", candidate)
                self.stdout.write(f"Searching base={base_dn!r} filter={ldap_filter!r}")
                try:
                    results = conn.search_s(
                        base_dn,
                        scope,
                        ldap_filter,
                        ["sAMAccountName", "userPrincipalName", "mail", "displayName", "distinguishedName"],
                    )
                except ldap.LDAPError as exc:
                    raise CommandError(f"LDAP search failed for {candidate!r}: {exc}") from exc

                clean_results = [item for item in results if item and item[0]]
                self.stdout.write(f"Search results for {candidate!r}: {len(clean_results)}")
                for dn, attrs in clean_results[:5]:
                    decoded = {}
                    for key, values in (attrs or {}).items():
                        decoded[key] = [
                            value.decode("utf-8", errors="replace") if isinstance(value, bytes) else str(value)
                            for value in values
                        ]
                    self.stdout.write(f"  DN: {dn}")
                    self.stdout.write(f"  Attrs: {decoded}")

        if options.get("auth"):
            if not username:
                raise CommandError("--auth requires a username argument.")
            from django.contrib.auth import authenticate

            password = getpass.getpass("AD user password: ")
            user = authenticate(username=username, password=password)
            if user is None:
                self.stdout.write(self.style.ERROR("Django authenticate() returned None."))
            else:
                self.stdout.write(self.style.SUCCESS(f"Django authenticate() OK: {user.username} email={user.email} active={user.is_active} staff={user.is_staff}"))
