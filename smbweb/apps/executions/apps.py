"""Executions app configuration."""
from typing import override

from django.apps import AppConfig


class ExecutionsConfig(AppConfig):
    """Configuration for Executions app."""

    default_auto_field = 'django.db.models.BigAutoField'
    name = 'smbweb.apps.executions'
    verbose_name = 'Executions'

    @override
    def ready(self) -> None:
        """Called when Django app is fully loaded.
        
        Can be used to set up signals, register callbacks, etc.
        """
        pass
