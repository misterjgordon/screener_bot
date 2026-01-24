"""Base viewset and mixins for common functionality."""
from typing import TYPE_CHECKING
from typing import Any
from typing import TypeVar
from typing import cast

from django.db.models import Model
from django.db.models import QuerySet
from rest_framework import status as http_status
from rest_framework import viewsets
from rest_framework.response import Response

if TYPE_CHECKING:
    from django.http import QueryDict
    from rest_framework.request import Request

QS = TypeVar('QS', bound=QuerySet)


class FilterMixin[M: Model]:
    """Mixin providing generic query param filtering."""

    filter_mappings: dict[str, str] = {}
    bool_filters: list[str] = []
    default_ordering: list[str] = []

    request: 'Request'

    def apply_filters(self, queryset: QS) -> QS:
        """Apply query param filters to queryset."""
        for param, field in self.filter_mappings.items():
            value = self.query_params.get(param)
            if value is not None:
                if param in self.bool_filters:
                    # Ensure value is a string (query_params.get() may return list)
                    str_value = value[0] if isinstance(value, list) else value
                    value = str_value.lower() == 'true'
                queryset = queryset.filter(**{field: value})

        if self.default_ordering:
            queryset = queryset.order_by(*self.default_ordering)

        return queryset

    @property
    def query_params(self) -> 'QueryDict':
        """Get query params from request."""
        return self.request.query_params

    @property
    def request_data(self) -> dict[str, Any]:
        """Get request body data."""
        data = self.request.data
        if isinstance(data, dict):
            return data
        # Convert QueryDict or other types to dict
        # In DRF, request.data is typically already a dict for JSON, but can be QueryDict for form data
        if not data:
            return {}
        # Type cast for type checker - runtime will handle QueryDict conversion
        try:
            return cast(dict[str, Any], dict(data.items()))  # type: ignore[arg-type]
        except (AttributeError, TypeError):
            return {}


class BaseViewSet[M: Model](FilterMixin[M], viewsets.ModelViewSet):
    """Base viewset with filtering, typed accessors, and response helpers."""

    def get_current_object(self) -> M:
        """Get the current object with proper typing."""
        return self.get_object()

    def get_required_fields(self, *fields: str) -> tuple[Any, ...] | Response:
        """Extract required fields from request data.

        Returns tuple of values if all present, or error Response if any missing.
        """
        values = []
        missing = []
        for field in fields:
            value = self.request_data.get(field)
            if value is None:
                missing.append(field)
            values.append(value)

        if missing:
            return self.error_response(
                f'{", ".join(missing)} {"is" if len(missing) == 1 else "are"} required',
            )
        return tuple(values)

    def toggle_field(self, field_name: str) -> Response:
        """Toggle a boolean field on the current object."""
        obj = self.get_current_object()
        new_value = not getattr(obj, field_name)
        setattr(obj, field_name, new_value)
        obj.save(update_fields=[field_name])
        return Response({'id': obj.pk, field_name: new_value})

    def success_response(self, **data: object) -> Response:
        """Return a success response."""
        return Response({'success': True, **data})

    def error_response(
        self,
        message: str,
        status_code: int = http_status.HTTP_400_BAD_REQUEST,
        **extra: object,
    ) -> Response:
        """Return an error response."""
        return Response({'error': message, **extra}, status=status_code)


class ReadOnlyBaseViewSet[M: Model](FilterMixin[M], viewsets.ReadOnlyModelViewSet):
    """Base viewset for read-only endpoints."""

    pass


class BaseActionViewSet(viewsets.ViewSet):
    """Base viewset for action-only endpoints (no model).

    Provides helper methods for request data access and response formatting.
    """

    request: 'Request'

    @property
    def query_params(self) -> 'QueryDict':
        """Get query params from request."""
        return self.request.query_params

    @property
    def request_data(self) -> dict[str, Any]:
        """Get request body data."""
        data = self.request.data
        if isinstance(data, dict):
            return data
        # Convert QueryDict or other types to dict
        # In DRF, request.data is typically already a dict for JSON, but can be QueryDict for form data
        if not data:
            return {}
        # Type cast for type checker - runtime will handle QueryDict conversion
        try:
            return cast(dict[str, Any], dict(data.items()))  # type: ignore[arg-type]
        except (AttributeError, TypeError):
            return {}

    def get_required_fields(self, *fields: str) -> tuple[Any, ...] | Response:
        """Extract required fields from request data.

        Returns tuple of values if all present, or error Response if any missing.
        """
        values = []
        missing = []
        for field in fields:
            value = self.request_data.get(field)
            if value is None:
                missing.append(field)
            values.append(value)

        if missing:
            return self.error_response(
                f'{", ".join(missing)} {"is" if len(missing) == 1 else "are"} required',
            )
        return tuple(values)

    def success_response(self, **data: object) -> Response:
        """Return a success response."""
        return Response({'success': True, **data})

    def error_response(
        self,
        message: str,
        status_code: int = http_status.HTTP_400_BAD_REQUEST,
        **extra: object,
    ) -> Response:
        """Return an error response."""
        return Response({'error': message, **extra}, status=status_code)
