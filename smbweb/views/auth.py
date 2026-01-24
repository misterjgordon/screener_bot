"""Auth views for user info endpoint."""
from typing import TYPE_CHECKING

from rest_framework.decorators import api_view
from rest_framework.response import Response

if TYPE_CHECKING:
    from rest_framework.request import Request


@api_view(['GET'])
def current_user(request: 'Request') -> Response:
    """Return current authenticated user info.

    Returns user data if authenticated, null otherwise.
    """
    if not request.user.is_authenticated:
        return Response(None)

    return Response({
        'id': request.user.id,
        'username': request.user.username,
        'is_staff': request.user.is_staff,
        'is_superuser': request.user.is_superuser,
    })
