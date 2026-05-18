"""
URL configuration for incident_management project.
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect


def root_redirect(request):
    return redirect('/accounts/login/')


urlpatterns = [
    path('', root_redirect),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('company/', include('companies.urls')),
    path('incidents/', include('incidents.urls')),
    path('api/', include('api.urls')),
]

if settings.SERVE_MEDIA_LOCALLY:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT
    )