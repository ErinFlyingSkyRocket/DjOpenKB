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

from kb.views import OpenKBLoginView, OpenKBLogoutView


urlpatterns = [
    # The site root is the public login entry point. Authenticated users are
    # redirected by OpenKBLoginView to LOGIN_REDIRECT_URL (/home/).
    path("", OpenKBLoginView.as_view(), name="root_login"),
    path("admin/", admin.site.urls),
    path("login/", OpenKBLoginView.as_view(), name="login"),
    path("logout/", OpenKBLogoutView.as_view(), name="logout"),
    path("", include("kb.urls")),
]
