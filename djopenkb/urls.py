"""
URL configuration for djopenkb project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import include, path

from kb.views.auth import OpenKBLoginView, OpenKBLogoutView, account_disabled, root_entry
from kb.admin_security import admin_mfa_verify


urlpatterns = [
    # Root URL is the login entry page.
    # Anonymous users see the Knowledge Repository login form here.
    # Authenticated users are sent to /home/.
    path("", root_entry, name="root_login"),

    # Admins must complete an extra MFA step before Django Admin.
    # This route must appear before admin.site.urls so /admin/mfa/verify/
    # is handled by our step-up view instead of Django Admin.
    path("admin/mfa/verify/", admin_mfa_verify, name="admin_mfa_verify"),
    path("admin/", admin.site.urls),
    path("login/", OpenKBLoginView.as_view(), name="login"),
    path("logout/", OpenKBLogoutView.as_view(), name="logout"),
    path("account-disabled/", account_disabled, name="account_disabled"),

    # All normal application pages live under kb.urls.
    # The old index page is intentionally moved to /home/.
    path("", include("kb.urls")),
]
