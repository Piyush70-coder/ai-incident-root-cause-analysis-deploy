from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token

# ✅ Import both views
from .views import IncidentViewSet, LogIngestionAPIView


# ✅ Router setup
router = DefaultRouter()
router.register(r'incidents', IncidentViewSet, basename='incident')


# ✅ URL patterns
urlpatterns = [
    # Router endpoints
    path('', include(router.urls)),

    # Auth token
    path('auth/token/', obtain_auth_token, name='api_token_auth'),

    # 🔥 Log ingestion endpoint (NEW)
    path('ingest/', LogIngestionAPIView.as_view(), name='log-ingest'),
]