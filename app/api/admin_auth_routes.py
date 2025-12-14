"""
Admin authentication routes for Google OAuth.

Handles OAuth flow, JWT token management, and user sessions.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.config import get_settings
from app.db.session import get_write_db
from app.services.admin_auth import AdminAuthService
from app.services.google_oauth import GoogleOAuthProvider

logger = get_logger(__name__)
router = APIRouter(prefix="/admin/oauth", tags=["admin-auth"])

# Singleton admin auth service instance (shared across all requests)
_admin_auth_service: AdminAuthService | None = None


def get_admin_auth_service() -> AdminAuthService:
    """Get admin auth service singleton instance."""
    global _admin_auth_service

    if _admin_auth_service is None:
        settings = get_settings()
        oauth_provider = GoogleOAuthProvider(
            client_id=settings.GOOGLE_CLIENT_ID,
            client_secret=settings.GOOGLE_CLIENT_SECRET,
            hd_domain="ciris.ai",
        )
        _admin_auth_service = AdminAuthService(
            oauth_provider=oauth_provider,
            jwt_secret=settings.ADMIN_JWT_SECRET,
            jwt_expire_hours=24,
        )

    return _admin_auth_service


@router.get("/login")
async def google_login(
    request: Request,
    redirect_uri: str | None = None,
    auth_service: AdminAuthService = Depends(get_admin_auth_service),
) -> RedirectResponse:
    """
    Initiate Google OAuth login flow.

    Query params:
        redirect_uri: Where to redirect after successful login (default: /admin)

    Returns:
        Redirect to Google OAuth consent screen
    """
    # Get the actual scheme (http or https) from proxy headers
    scheme = request.headers.get("X-Forwarded-Proto", request.url.scheme)
    host = request.headers.get("Host", request.url.hostname)
    base_url = f"{scheme}://{host}"

    # Default redirect to admin UI
    if not redirect_uri:
        redirect_uri = f"{base_url}/admin-ui/"

    # Build callback URL (where Google will redirect back to)
    callback_url = f"{base_url}/admin/oauth/callback"

    try:
        state, auth_url = await auth_service.initiate_oauth_flow(
            redirect_uri=redirect_uri, callback_url=callback_url
        )

        logger.info(
            "oauth_login_initiated",
            state=state[:8],
            redirect_uri=redirect_uri,
            callback_url=callback_url,
            request_scheme=request.url.scheme,
            base_url=str(request.base_url),
        )

        return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)

    except Exception as e:
        logger.error("oauth_login_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to initiate OAuth login",
        ) from e


@router.get("/callback")
async def google_callback(
    code: str,
    state: str,
    response: Response,
    db: AsyncSession = Depends(get_write_db),
    auth_service: AdminAuthService = Depends(get_admin_auth_service),
) -> RedirectResponse:
    """
    Handle Google OAuth callback.

    Query params:
        code: Authorization code from Google
        state: State token for CSRF protection

    Returns:
        Redirect to admin UI with JWT token in cookie and URL param
    """
    try:
        # Complete OAuth flow
        result = await auth_service.handle_oauth_callback(code, state, db)

        # Type assertion: user is always a dict with user info
        user_info = result["user"]
        assert isinstance(user_info, dict)
        logger.info(
            "oauth_callback_success",
            user_email=user_info["email"],
            user_role=user_info["role"],
        )

        # Extract string values (type assertions for mypy)
        access_token = result["access_token"]
        redirect_uri = result["redirect_uri"]
        assert isinstance(access_token, str)
        assert isinstance(redirect_uri, str)

        # Set HttpOnly cookie for browser
        response_redirect = RedirectResponse(
            url=f"{redirect_uri}?token={access_token}",
            status_code=status.HTTP_302_FOUND,
        )

        response_redirect.set_cookie(
            key="admin_token",
            value=access_token,
            httponly=True,
            secure=True,  # HTTPS only
            samesite="lax",
            max_age=86400,  # 24 hours
        )

        return response_redirect

    except ValueError as e:
        logger.warning("oauth_callback_failed", error=str(e))
        # Redirect to login with error
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth authentication failed: {str(e)}",
        ) from e

    except Exception as e:
        logger.error("oauth_callback_error", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OAuth authentication error",
        ) from e


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    """
    Logout current admin user.

    Clears the JWT cookie.

    Returns:
        Success message
    """
    response.delete_cookie(key="admin_token")

    logger.info("admin_user_logout")

    return {"message": "Logged out successfully"}


@router.get("/user")
async def get_current_user(
    request: Request,
    authorization: str | None = None,
    db: AsyncSession = Depends(get_write_db),
    auth_service: AdminAuthService = Depends(get_admin_auth_service),
) -> dict[str, str | None]:
    """
    Get current authenticated admin user info.

    Checks Authorization header first, then cookie.

    Returns:
        User info (id, email, name, picture, role)
    """
    # Try Authorization header first
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")

    # Try cookie if no header
    if not token:
        token = request.cookies.get("admin_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    # Verify JWT
    payload = auth_service.verify_jwt_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    # Get user from database
    from uuid import UUID

    user_id = UUID(str(payload["sub"]))
    admin_user = await auth_service.get_admin_user_by_id(db, user_id)

    if not admin_user or not admin_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User not authorized",
        )

    return {
        "id": str(admin_user.id),
        "email": admin_user.email,
        "name": admin_user.full_name,
        "picture": admin_user.picture_url,
        "role": admin_user.role,
    }
