"""
URL configuration for incident_management project.
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('company/', include('companies.urls')),
    path('', include('incidents.urls')),
    path('api/', include('api.urls')),
]

if settings.SERVE_MEDIA_LOCALLY:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
