"""
Admin authentication service using Google OAuth.

Simplified from CIRISManager - stores admin users in PostgreSQL.
"""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.db.models import AdminUser
from app.models.domain import OAuthSession, OAuthToken, OAuthUser
from app.services.google_oauth import GoogleOAuthProvider

logger = get_logger(__name__)


class AdminAuthService:
    """Admin authentication service."""

    def __init__(
        self,
        oauth_provider: GoogleOAuthProvider,
        jwt_secret: str,
        jwt_expire_hours: int = 24,
    ):
        self.oauth_provider = oauth_provider
        self.jwt_secret = jwt_secret
        self.jwt_expire_hours = jwt_expire_hours
        self._sessions: dict[str, OAuthSession] = {}  # In-memory session store

    async def initiate_oauth_flow(self, redirect_uri: str, callback_url: str) -> tuple[str, str]:
        """
        Initiate OAuth flow.

        Returns:
            (state, auth_url) tuple
        """
        state = secrets.token_urlsafe(32)
        session = OAuthSession(
            redirect_uri=redirect_uri,
            callback_url=callback_url,
            created_at=datetime.now(UTC).isoformat(),
        )

        self._sessions[state] = session
        auth_url = await self.oauth_provider.get_authorization_url(state, callback_url)

        logger.info(
            "oauth_flow_initiated",
            state=state[:8],
            callback_url=callback_url,
            auth_url_preview=auth_url[:150],
        )
        return state, auth_url

    async def handle_oauth_callback(
        self, code: str, state: str, db: AsyncSession
    ) -> dict[str, str | dict[str, str | None]]:
        """
        Handle OAuth callback.

        Returns:
            dict with access_token, redirect_uri, user
        """
        # Get session
        session = self._sessions.get(state)
        if not session:
            logger.warning("invalid_oauth_state", state=state[:8])
            raise ValueError("Invalid OAuth state")

        # Exchange code for token
        token: OAuthToken = await self.oauth_provider.exchange_code_for_token(
            code, session.callback_url
        )

        # Get user info
        user: OAuthUser = await self.oauth_provider.get_user_info(token.access_token)

        logger.info("oauth_user_info_received", email=user.email)

        # Create or update admin user in database
        admin_user = await self._get_or_create_admin_user(db, user)

        # Update last login
        admin_user.last_login_at = datetime.now(UTC)
        await db.commit()

        # Generate JWT
        jwt_token = self._create_jwt_token(admin_user)

        # Clean up session
        del self._sessions[state]

        logger.info(
            "oauth_login_success",
            email=admin_user.email,
            role=admin_user.role,
            user_id=str(admin_user.id),
        )

        return {
            "access_token": jwt_token,
            "redirect_uri": session.redirect_uri,
            "user": {
                "id": str(admin_user.id),
                "email": admin_user.email,
                "name": admin_user.full_name,
                "picture": admin_user.picture_url,
                "role": admin_user.role,
            },
        }

    async def _get_or_create_admin_user(self, db: AsyncSession, oauth_user: OAuthUser) -> AdminUser:
        """Get existing admin user or create if doesn't exist."""
        stmt = select(AdminUser).where(AdminUser.email == oauth_user.email)
        result = await db.execute(stmt)
        admin_user = result.scalar_one_or_none()

        if admin_user:
            # Update profile if changed
            if oauth_user.name and admin_user.full_name != oauth_user.name:
                admin_user.full_name = oauth_user.name
            if oauth_user.picture and admin_user.picture_url != oauth_user.picture:
                admin_user.picture_url = oauth_user.picture
            if not admin_user.google_id and oauth_user.id:
                admin_user.google_id = oauth_user.id

            # Check if user is active
            if not admin_user.is_active:
                logger.warning("inactive_user_login_attempt", email=oauth_user.email)
                raise ValueError(f"User account {oauth_user.email} is deactivated")

            return admin_user

        # Check if first user (eric@ciris.ai) or new user
        stmt_count = select(AdminUser)
        result_count = await db.execute(stmt_count)
        user_count = len(result_count.scalars().all())

        # Determine role: eric@ciris.ai is always admin, others are viewer by default
        role = "admin" if oauth_user.email == "eric@ciris.ai" else "viewer"

        # If this is the first user and it's eric@ciris.ai, make them admin
        if user_count == 0 and oauth_user.email == "eric@ciris.ai":
            logger.info("creating_first_admin_user", email=oauth_user.email)
            role = "admin"
        elif user_count == 0:
            # First user but not eric@ciris.ai - shouldn't happen, but make them viewer
            logger.warning("first_user_not_eric", email=oauth_user.email)
            role = "viewer"

        # Create new admin user
        new_admin = AdminUser(
            id=uuid4(),
            email=oauth_user.email,
            google_id=oauth_user.id,
            full_name=oauth_user.name or oauth_user.email.split("@")[0],
            picture_url=oauth_user.picture,
            role=role,
            is_active=True,
        )

        db.add(new_admin)
        await db.commit()
        await db.refresh(new_admin)

        logger.info(
            "new_admin_user_created",
            email=new_admin.email,
            role=new_admin.role,
            user_id=str(new_admin.id),
        )

        return new_admin

    def _create_jwt_token(self, admin_user: AdminUser) -> str:
        """Create JWT token for admin user."""
        now = datetime.now(UTC)
        payload = {
            "sub": str(admin_user.id),
            "email": admin_user.email,
            "role": admin_user.role,
            "iat": now,
            "exp": now + timedelta(hours=self.jwt_expire_hours),
        }

        return jwt.encode(payload, self.jwt_secret, algorithm="HS256")

    def verify_jwt_token(self, token: str) -> dict[str, str | int] | None:
        """Verify JWT token and return payload."""
        try:
            payload: dict[str, str | int] = jwt.decode(token, self.jwt_secret, algorithms=["HS256"])
            return payload
        except jwt.ExpiredSignatureError:
            logger.warning("jwt_token_expired")
            return None
        except jwt.InvalidTokenError as e:
            logger.warning("jwt_token_invalid", error=str(e))
            return None

    async def get_admin_user_by_id(self, db: AsyncSession, user_id: UUID) -> AdminUser | None:
        """Get admin user by ID."""
        stmt = select(AdminUser).where(AdminUser.id == user_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()
