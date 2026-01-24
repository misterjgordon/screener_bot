"""Serializers for executions API."""
from rest_framework import serializers

from smbweb.apps.executions.models import Execution


class ExecutionSerializer(serializers.ModelSerializer):
    """Serializer for Execution model."""
    
    class Meta:
        model = Execution
        fields = '__all__'
        read_only_fields = ['id', 'timestamp']
