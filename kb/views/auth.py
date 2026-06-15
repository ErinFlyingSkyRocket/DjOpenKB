from django.views.decorators.cache import never_cache
from .services import *
from ..auth_monitoring import (
    format_retry_after,
    get_auth_lockout_status,
    log_auth_event,
    record_auth_failure,
    record_auth_success,
)
from ..mfa import (
    begin_pending_mfa_login,
    clear_mfa_verified,
    clear_pending_mfa_login,
    get_or_create_mfa_device,
    mfa_is_verified,
    user_requires_mfa,
    verify_totp_code,
)
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse
from django.utils.translation import gettext as _
from urllib.parse import urlencode


@never_cache
def root_entry(request):
    """Site root entry point.

    Anonymous users see the normal DjOpenKB login page at /. Authenticated
    users are sent to the actual article index at /home/. Keeping this as an
    explicit view prevents the old index view from being exposed at the root
    URL by accident.
    """
    if request.user.is_authenticated:
        return redirect("home")
    return OpenKBLoginView.as_view()(request)


class OpenKBLoginView(LoginView):
    template_name = "login.html"
    redirect_authenticated_user = False

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if not user_can_access_main_site(request.user):
                logout(request)
                return redirect("root_login")

            if user_requires_mfa(request.user):
                if mfa_is_verified(request):
                    return redirect(self.get_success_url())

                user = request.user
                device = get_or_create_mfa_device(user)
                next_url = self.get_success_url()
                backend = request.session.get("_auth_user_backend") or getattr(user, "backend", None)
                logout(request)
                begin_pending_mfa_login(
                    request,
                    user,
                    next_url=next_url,
                    backend=backend,
                )
                if device.confirmed:
                    return redirect(f"{reverse('mfa_verify')}?{urlencode({'next': next_url})}")
                return redirect(f"{reverse('mfa_setup')}?{urlencode({'next': next_url})}")

            return redirect(self.get_success_url())

        if request.method == "POST":
            username = (request.POST.get("username") or "").strip()
            locked, retry_after, identifier = get_auth_lockout_status(
                request,
                username=username,
                purpose="password",
            )
            if locked:
                log_auth_event(
                    request,
                    event_type="password_failure",
                    success=False,
                    username=username,
                    login_mode=(request.POST.get("login_mode") or "").strip().lower(),
                    details={
                        "reason": "temporary_lockout",
                        "lockout_identifier": identifier,
                        "retry_after_seconds": retry_after,
                    },
                )
                messages.error(
                    request,
                    _("Too many failed sign-in attempts. Please try again in %(duration)s.")
                    % {"duration": format_retry_after(retry_after)},
                )
                form = self.get_form()
                return self.render_to_response(self.get_context_data(form=form))

        return super().dispatch(request, *args, **kwargs)

    def form_invalid(self, form):
        if not getattr(self.request, "_skip_auth_failure_log", False):
            username = (self.request.POST.get("username") or "").strip()
            lockout = record_auth_failure(
                self.request,
                username=username,
                purpose="password",
            )
            details = {
                "reason": "invalid_credentials",
                "lockout_identifier": lockout.get("identifier"),
                "failure_count": lockout.get("failure_count"),
                "failure_limit": lockout.get("failure_limit"),
            }
            if lockout.get("locked"):
                details["reason"] = "temporary_lockout_created"
                details["retry_after_seconds"] = lockout.get("retry_after_seconds")
                messages.error(
                    self.request,
                    _("Too many failed sign-in attempts. Please try again in %(duration)s.")
                    % {"duration": format_retry_after(lockout.get("retry_after_seconds"))},
                )

            log_auth_event(
                self.request,
                event_type="password_failure",
                success=False,
                username=username,
                login_mode=(self.request.POST.get("login_mode") or "").strip().lower(),
                details=details,
            )
        return super().form_invalid(form)

    def form_valid(self, form):
        user = form.get_user()
        login_mode = (self.request.POST.get("login_mode") or "").strip().lower()
        if not user_can_access_main_site(user):
            log_auth_event(
                self.request,
                event_type="password_failure",
                success=False,
                user=user,
                username=user.get_username(),
                login_mode=login_mode,
                details={"reason": "main_site_access_blocked"},
            )
            logout(self.request)
            self.request._skip_auth_failure_log = True
            return self.form_invalid(form)

        record_auth_success(
            self.request,
            username=user.get_username(),
            user=user,
            purpose="password",
        )

        log_auth_event(
            self.request,
            event_type="password_success",
            success=True,
            user=user,
            username=user.get_username(),
            login_mode=login_mode,
        )

        if user_requires_mfa(user):
            # MFA is part of login completion. Do not create the real
            # Django login session yet. Store a pending-MFA session and only log
            # the user in after setup/verification succeeds.
            clear_pending_mfa_login(self.request)
            clear_mfa_verified(self.request)
            device = get_or_create_mfa_device(user)
            begin_pending_mfa_login(
                self.request,
                user,
                next_url=self.get_success_url(),
                backend=getattr(user, "backend", None),
            )
            log_auth_event(
                self.request,
                event_type="pending_mfa",
                success=True,
                user=user,
                username=user.get_username(),
                login_mode=login_mode,
                details={"device_confirmed": bool(device.confirmed)},
            )
            if device.confirmed:
                return redirect("mfa_verify")
            return redirect("mfa_setup")

        return super().form_valid(form)


class OpenKBLogoutView(LogoutView):
    """Logout view that prevents browser back/forward cache from showing stale pages."""
    next_page = "root_login"

    def dispatch(self, request, *args, **kwargs):
        from kb.middleware import set_strict_no_cache_headers
        from kb.mfa import clear_mfa_verified, clear_pending_mfa_login

        logout_user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
        if logout_user:
            log_auth_event(request, event_type="logout", success=True, user=logout_user, username=logout_user.get_username())

        clear_mfa_verified(request)
        clear_pending_mfa_login(request)
        response = super().dispatch(request, *args, **kwargs)
        set_strict_no_cache_headers(response)
        response["Clear-Site-Data"] = '"cache"'
        return response


@require_POST
def set_site_language(request):
    """Set the active UI language from the navbar dropdown.

    Anonymous users store the choice in the django_language cookie.
    Logged-in users also sync the same choice to their UserProfile.
    """
    language_code = (request.POST.get("language") or "").strip().lower()
    allowed_codes = {code for code, _name in settings.LANGUAGES}

    if language_code not in allowed_codes:
        language_code = settings.LANGUAGE_CODE

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("home")
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = reverse("home")

    if request.user.is_authenticated:
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.preferred_language = language_code
        profile.save(update_fields=["preferred_language", "updated_at"])

    translation.activate(language_code)
    request.LANGUAGE_CODE = language_code

    response = redirect(next_url)
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        language_code,
        max_age=60 * 60 * 24 * 365,
        samesite="Lax",
    )
    return response


def _verify_profile_mfa_code(request, user):
    """Require a fresh MFA/OTP code before sensitive profile changes.

    This is only used by the normal website profile page. It does not affect
    Django admin, where administrators manage users through the admin site.
    """
    if not user_requires_mfa(user):
        return True

    device = getattr(user, "kb_mfa_device", None)
    if not device or not device.confirmed:
        messages.error(request, _("Set up MFA before changing sensitive account details."))
        return False

    code = request.POST.get("mfa_code", "")
    locked, retry_after, identifier = get_auth_lockout_status(
        request,
        user=user,
        purpose="mfa",
    )
    if locked:
        log_auth_event(
            request,
            event_type="mfa_verify_failure",
            success=False,
            user=user,
            username=user.get_username(),
            details={
                "reason": "temporary_lockout",
                "lockout_identifier": identifier,
                "retry_after_seconds": retry_after,
            },
        )
        messages.error(
            request,
            _("Too many incorrect MFA codes. Please try again in %(duration)s.")
            % {"duration": format_retry_after(retry_after)},
        )
        return False

    if not verify_totp_code(device, code):
        lockout = record_auth_failure(request, user=user, purpose="mfa")
        details = {
            "reason": "invalid_profile_change_totp",
            "lockout_identifier": lockout.get("identifier"),
            "failure_count": lockout.get("failure_count"),
            "failure_limit": lockout.get("failure_limit"),
        }
        if lockout.get("locked"):
            details["reason"] = "temporary_lockout_created"
            details["retry_after_seconds"] = lockout.get("retry_after_seconds")
        log_auth_event(
            request,
            event_type="mfa_verify_failure",
            success=False,
            user=user,
            username=user.get_username(),
            details=details,
        )
        messages.error(request, _("MFA/OTP code is incorrect."))
        return False

    record_auth_success(request, user=user, purpose="mfa")
    device.mark_verified()
    log_auth_event(
        request,
        event_type="mfa_verify_success",
        success=True,
        user=user,
        username=user.get_username(),
        details={"reason": "profile_sensitive_change_confirmed"},
    )
    return True


@main_site_login_required
def profile(request):
    return render(request, "profile.html", get_profile_account_context(request.user))


@main_site_login_required
@require_POST
def update_profile(request):
    if request.method != "POST":
        return redirect("profile")

    user = request.user
    user_is_ldap_managed = is_ldap_managed_user(user)
    profile_action = request.POST.get("profile_action", "").strip()

    if profile_action == "language":
        language_code = request.POST.get("preferred_language", "").strip()
        allowed_codes = {code for code, _name in settings.LANGUAGES}

        if language_code not in allowed_codes:
            messages.error(request, _("Invalid language selected."))
            return redirect("profile")

        profile, created = UserProfile.objects.get_or_create(user=user)
        profile.preferred_language = language_code
        profile.save(update_fields=["preferred_language", "updated_at"])

        translation.activate(language_code)
        request.LANGUAGE_CODE = language_code

        messages.success(request, _("Language preference updated successfully."))
        response = redirect("profile")
        response.set_cookie(
            settings.LANGUAGE_COOKIE_NAME,
            language_code,
            max_age=60 * 60 * 24 * 365,
            samesite="Lax",
        )
        return response

    if profile_action == "username":
        messages.error(request, _("Username changes are managed by administrators."))
        return redirect("profile")

    if profile_action == "email":
        if user_is_ldap_managed:
            messages.error(request, _("This email address is managed by your domain account and cannot be changed here."))
            return redirect("profile")

        if user.has_usable_password():
            current_password = request.POST.get("current_password", "")
            if not user.check_password(current_password):
                messages.error(request, _("Confirm password is incorrect."))
                return redirect("profile")

        if not _verify_profile_mfa_code(request, user):
            return redirect("profile")

        email = request.POST.get("email", "").strip()
        user.email = email
        user.save(update_fields=["email"])
        messages.success(request, _("Email updated successfully."))
        return redirect("profile")

    messages.error(request, _("Invalid profile update request."))
    return redirect("profile")


@main_site_login_required
@require_POST
def change_password(request):
    if request.method != "POST":
        return redirect("profile")

    user = request.user

    if is_ldap_managed_user(user) or not user.has_usable_password():
        messages.error(request, "Password syncs with your company password. Please change it through the company password system.")
        return redirect("profile")

    old_password = request.POST.get("old_password", "")
    new_password1 = request.POST.get("new_password1", "")
    new_password2 = request.POST.get("new_password2", "")

    if not user.check_password(old_password):
        messages.error(request, "Old password is incorrect.")
        return redirect("profile")

    if new_password1 != new_password2:
        messages.error(request, "New password and confirm password do not match.")
        return redirect("profile")

    policy_issues = validate_profile_password_policy(new_password1, user)
    if policy_issues:
        messages.error(request, " ".join(policy_issues))
        return redirect("profile")

    try:
        validate_password(new_password1, user=user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("profile")

    if not _verify_profile_mfa_code(request, user):
        return redirect("profile")

    user.set_password(new_password1)
    user.save(update_fields=["password"])
    update_session_auth_hash(request, user)
    messages.success(request, "Password changed successfully.")
    return redirect("profile")
