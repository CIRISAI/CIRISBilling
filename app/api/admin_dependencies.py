"""
Admin authentication dependencies for protecting admin routes.

Provides FastAPI dependencies for JWT validation and role checking.
"""

from uuid import UUID

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.config import get_settings
from app.db.models import AdminUser
from app.db.session import get_write_db
from app.services.admin_auth import AdminAuthService
from app.services.google_oauth import GoogleOAuthProvider

logger = get_logger(__name__)


def get_admin_auth_service() -> AdminAuthService:
    """Get admin auth service instance."""
    settings = get_settings()
    oauth_provider = GoogleOAuthProvider(
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        hd_domain="ciris.ai",
    )
    return AdminAuthService(
        oauth_provider=oauth_provider,
        jwt_secret=settings.ADMIN_JWT_SECRET,
        jwt_expire_hours=24,
    )


async def get_current_admin(
    request: Request,
    authorization: str | None = Header(None),
    db: AsyncSession = Depends(get_write_db),
    auth_service: AdminAuthService = Depends(get_admin_auth_service),
) -> AdminUser:
    """
    Get current authenticated admin user.

    Checks Authorization header first, then cookie.
    Validates JWT token and retrieves user from database.

    Raises:
        HTTPException(401): If no token provided or token is invalid
        HTTPException(403): If user account is deactivated

    Returns:
        AdminUser: The authenticated admin user
    """
    # Try Authorization header first
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")

    # Try cookie if no header
    if not token:
        token = request.cookies.get("admin_token")

    if not token:
        logger.warning("admin_auth_no_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify JWT
    payload = auth_service.verify_jwt_token(token)
    if not payload:
        logger.warning("admin_auth_invalid_token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Get admin user from database
    try:
        user_id = UUID(str(payload["sub"]))
    except (ValueError, KeyError) as e:
        logger.warning("admin_auth_invalid_user_id", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        ) from e

    admin_user = await auth_service.get_admin_user_by_id(db, user_id)

    if not admin_user:
        logger.warning("admin_auth_user_not_found", user_id=str(user_id))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not admin_user.is_active:
        logger.warning("admin_auth_user_inactive", user_id=str(user_id), email=admin_user.email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is deactivated",
        )

    logger.debug(
        "admin_auth_success",
        user_id=str(admin_user.id),
        email=admin_user.email,
        role=admin_user.role,
    )

    return admin_user


async def require_admin_role(
    admin: AdminUser = Depends(get_current_admin),
) -> AdminUser:
    """
    Require admin role (not just viewer).

    Use this dependency for routes that require write/modify permissions.
    Viewers can only read data.

    Raises:
        HTTPException(403): If user is not an admin

    Returns:
        AdminUser: The authenticated admin user with admin role
    """
    if admin.role != "admin":
        logger.warning(
            "admin_auth_insufficient_role",
            user_id=str(admin.id),
            email=admin.email,
            role=admin.role,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required. Your role: viewer (read-only)",
        )

    return admin
