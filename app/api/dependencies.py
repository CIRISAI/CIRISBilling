"""
FastAPI Dependencies - Authentication and authorization.

NO DICTIONARIES - All dependencies return typed objects.
"""

from fastapi import Header, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_write_db
from app.services.api_key import APIKeyService, APIKeyData
from app.exceptions import AuthenticationError, AuthorizationError


async def get_api_key(
    x_api_key: str = Header(..., description="Agent API key"),
    db: AsyncSession = Depends(get_write_db),
) -> APIKeyData:
    """
    FastAPI dependency to validate API key from X-API-Key header.

    Usage:
        @router.post("/v1/billing/charges")
        async def create_charge(
            request: CreateChargeRequest,
            api_key: APIKeyData = Depends(get_api_key)
        ):
            # api_key is validated and contains permissions
            pass

    Returns:
        APIKeyData if valid

    Raises:
        HTTPException 401 if invalid
    """
    api_key_service = APIKeyService(db)

    try:
        api_key_data = await api_key_service.validate_api_key(x_api_key)
        return api_key_data
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "ApiKey"},
        ) from exc


def require_permission(required_permission: str):
    """
    FastAPI dependency factory to check specific permission.

    Usage:
        @router.post("/v1/billing/charges")
        async def create_charge(
            request: CreateChargeRequest,
            api_key: APIKeyData = Depends(require_permission("billing:write"))
        ):
            pass

    Args:
        required_permission: Permission string (e.g., "billing:write")

    Returns:
        Dependency function that checks permission
    """

    async def permission_checker(
        api_key: APIKeyData = Depends(get_api_key),
    ) -> APIKeyData:
        """Check if API key has required permission."""
        if required_permission not in api_key.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {required_permission}",
            )
        return api_key

    return permission_checker
