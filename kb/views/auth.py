from .services import *
from ..mfa import (
    begin_pending_mfa_login,
    clear_mfa_verified,
    clear_pending_mfa_login,
    get_or_create_mfa_device,
    user_requires_mfa,
)
from django.contrib.auth import logout
from django.contrib.auth.views import LoginView, LogoutView
from django.utils.translation import gettext as _


class OpenKBLoginView(LoginView):
    template_name = "login.html"
    redirect_authenticated_user = False

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if not user_can_access_main_site(request.user):
                logout(request)
                return redirect("home")

            if user_requires_mfa(request.user):
                clear_mfa_verified(request)
                device = get_or_create_mfa_device(request.user)
                if device.confirmed:
                    return redirect("mfa_verify")
                return redirect("mfa_setup")

            return redirect(self.get_success_url())

        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        if not user_can_access_main_site(user):
            logout(self.request)
            return self.form_invalid(form)

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
            if device.confirmed:
                return redirect("mfa_verify")
            return redirect("mfa_setup")

        return super().form_valid(form)


class OpenKBLogoutView(LogoutView):
    """Logout view that prevents browser back/forward cache from showing stale pages."""
    next_page = "login"

    def dispatch(self, request, *args, **kwargs):
        from kb.middleware import set_strict_no_cache_headers
        from kb.mfa import clear_mfa_verified, clear_pending_mfa_login

        clear_mfa_verified(request)
        clear_pending_mfa_login(request)
        response = super().dispatch(request, *args, **kwargs)
        set_strict_no_cache_headers(response)
        response["Clear-Site-Data"] = '"cache"'
        return response


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


def profile(request):
    return render(request, "profile.html", get_profile_account_context(request.user))


def update_profile(request):
    if request.method != "POST":
        return redirect("profile")

    User = get_user_model()
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

    # For Django local accounts, require the current password before changing
    # username/email. LDAP users normally do not have a local usable password,
    # so LDAP-managed fields are protected by backend rules instead.
    if user.has_usable_password():
        current_password = request.POST.get("current_password", "")
        if not user.check_password(current_password):
            messages.error(request, _("Confirm password is incorrect."))
            return redirect("profile")

    if profile_action == "username":
        username = request.POST.get("username", "").strip()
        if not username:
            messages.error(request, _("Username cannot be empty."))
            return redirect("profile")

        username_exists = User.objects.exclude(pk=user.pk).filter(username__iexact=username).exists()
        if username_exists:
            messages.error(request, _("That username is already used by another account."))
            return redirect("profile")

        user.username = username
        user.save(update_fields=["username"])
        messages.success(request, _("Username updated successfully."))
        return redirect("profile")

    if profile_action == "email":
        if user_is_ldap_managed:
            messages.error(request, _("This email address is managed by your domain account and cannot be changed here."))
            return redirect("profile")

        email = request.POST.get("email", "").strip()
        user.email = email
        user.save(update_fields=["email"])
        messages.success(request, _("Email updated successfully."))
        return redirect("profile")

    messages.error(request, _("Invalid profile update request."))
    return redirect("profile")


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

    user.set_password(new_password1)
    user.save(update_fields=["password"])
    update_session_auth_hash(request, user)
    messages.success(request, "Password changed successfully.")
    return redirect("edit_my_suggestions")
