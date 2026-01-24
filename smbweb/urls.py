"""URL configuration for smbweb API."""
from django.urls import include
from django.urls import path

from smbweb.views.auth import current_user

urlpatterns = [
    path('api/', include('smbweb.apps.executions.urls')),
    path('api/auth/me/', current_user),
]
