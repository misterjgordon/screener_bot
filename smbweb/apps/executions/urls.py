"""URL configuration for executions API."""
from rest_framework.routers import DefaultRouter

from smbweb.apps.executions.views.executions import ExecutionViewSet

router = DefaultRouter()
router.register(r'executions', ExecutionViewSet, basename='execution')

urlpatterns = router.urls
