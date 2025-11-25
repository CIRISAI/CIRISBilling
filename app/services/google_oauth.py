"""
Google OAuth provider for admin authentication.

Adapted from CIRISManager implementation for CIRIS Billing.
"""

import httpx
from structlog import get_logger

from app.models.domain import OAuthToken, OAuthUser

logger = get_logger(__name__)


class GoogleOAuthProvider:
    """Google OAuth provider implementation."""

    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        hd_domain: str = "ciris.ai",  # Restrict to @ciris.ai
        http_client: httpx.AsyncClient | None = None,
    ):
        self.client_id = client_id
        self.client_secret = client_secret
        self.hd_domain = hd_domain
        self._http_client = http_client

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Get HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient()
        return self._http_client

    async def get_authorization_url(self, state: str, redirect_uri: str) -> str:
        """Get OAuth authorization URL."""
        from urllib.parse import urlencode

        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "access_type": "offline",
            "prompt": "select_account",
            "hd": self.hd_domain,  # Restrict to ciris.ai domain
        }

        query_string = urlencode(params)
        return f"{self.AUTH_URL}?{query_string}"

    async def exchange_code_for_token(self, code: str, redirect_uri: str) -> OAuthToken:
        """Exchange authorization code for access token."""
        data = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }

        try:
            response = await self.http_client.post(self.TOKEN_URL, data=data)
            response.raise_for_status()
            token_data = response.json()

            return OAuthToken(
                access_token=token_data["access_token"],
                token_type=token_data.get("token_type", "Bearer"),
                expires_in=token_data.get("expires_in"),
                refresh_token=token_data.get("refresh_token"),
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                "token_exchange_failed", status=e.response.status_code, text=e.response.text
            )
            raise ValueError(f"Failed to exchange code: {e.response.status_code}")
        except Exception as e:
            logger.error("token_exchange_error", error=str(e))
            raise ValueError("Failed to exchange authorization code")

    async def get_user_info(self, access_token: str) -> OAuthUser:
        """Get user information from Google."""
        try:
            response = await self.http_client.get(
                self.USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
            )
            response.raise_for_status()
            user_data = response.json()

            # Verify user is from @ciris.ai domain
            email = user_data.get("email", "")
            if not email.endswith("@ciris.ai"):
                logger.warning("unauthorized_domain_attempt", email=email)
                raise ValueError(f"Only @ciris.ai emails are allowed. Got: {email}")

            return OAuthUser(
                id=user_data["id"],
                email=email,
                name=user_data.get("name"),
                picture=user_data.get("picture"),
            )

        except httpx.HTTPStatusError as e:
            logger.error(
                "user_info_fetch_failed", status=e.response.status_code, text=e.response.text
            )
            raise ValueError(f"Failed to get user info: {e.response.status_code}")
        except ValueError:
            # Re-raise domain validation errors
            raise
        except Exception as e:
            logger.error("user_info_error", error=str(e))
            raise ValueError("Failed to get user information")

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
