from .services import *


class OpenKBLoginView(LoginView):
    template_name = "login.html"
    redirect_authenticated_user = False

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            if user_can_access_main_site(request.user):
                return redirect(self.get_success_url())
            logout(request)
            return redirect("home")

        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.get_user()
        if not user_can_access_main_site(user):
            logout(self.request)
            return self.form_invalid(form)
        return super().form_valid(form)


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
