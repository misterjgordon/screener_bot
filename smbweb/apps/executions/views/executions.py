"""Views for executions API."""
from rest_framework import viewsets

from smbweb.apps.executions.models import Execution
from smbweb.apps.executions.serializers import ExecutionSerializer
from smbweb.views.base_viewset import BaseViewSet


class ExecutionViewSet(BaseViewSet[Execution]):
    """ViewSet for managing executions."""
    
    serializer_class = ExecutionSerializer
    
    def get_queryset(self):
        """Return queryset for executions."""
        return Execution.objects.all()  # type: ignore[attr-defined]
    
    # Filter mappings for query parameters
    filter_mappings = {
        'trader': 'trader',
        'symbol': 'symbol',
        'change_type': 'change_type',
        'net_side': 'net_side',
    }
    
    # Default ordering
    default_ordering = ['-timestamp']
