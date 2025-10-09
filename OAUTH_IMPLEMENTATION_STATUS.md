# CIRIS Billing - OAuth Implementation Status

**Date:** 2025-10-08
**Status:** Foundation Complete - Integration Pending
**Authentication Method:** Google OAuth (restricted to @ciris.ai domain)

---

## ✅ Completed Components

### 1. **Database Model Updates**

**File:** `app/db/models.py`

**AdminUser model updated:**
- ✅ Removed password_hash field (using OAuth now)
- ✅ Removed MFA fields (Google handles this)
- ✅ Added `google_id` field for OAuth identity
- ✅ Added `picture_url` field for profile image
- ✅ Simplified roles to 2: `admin` and `viewer`
- ✅ Added database constraint: `email LIKE '%@ciris.ai'`
- ✅ Added CHECK constraint for roles: `IN ('admin', 'viewer')`

**Key changes:**
```sql
-- New fields
google_id VARCHAR(255) UNIQUE
picture_url VARCHAR(512)

-- Removed fields
password_hash (no longer needed)
mfa_enabled, mfa_secret (Google handles MFA)

-- Updated constraints
CHECK (role IN ('admin', 'viewer'))  -- Simplified from 3 to 2 roles
CHECK (email LIKE '%@ciris.ai')      -- Domain restriction
```

### 2. **OAuth Domain Models**

**File:** `app/models/domain.py`

Created immutable dataclasses for OAuth:

```python
@dataclass(frozen=True)
class OAuthToken:
    access_token: str
    token_type: str = "Bearer"
    expires_in: int | None = None
    refresh_token: str | None = None

@dataclass(frozen=True)
class OAuthUser:
    id: str
    email: str
    name: str | None = None
    picture: str | None = None

    def __post_init__(self):
        # Validates email ends with @ciris.ai
        if not self.email.endswith("@ciris.ai"):
            raise ValueError("Only @ciris.ai emails allowed")

@dataclass(frozen=True)
class OAuthSession:
    redirect_uri: str
    callback_url: str
    created_at: str
```

### 3. **Google OAuth Provider**

**File:** `app/services/google_oauth.py`

OAuth provider implementation:
- ✅ Authorization URL generation with `hd=ciris.ai` parameter
- ✅ Token exchange (code → access token)
- ✅ User info fetching from Google API
- ✅ Domain validation (@ciris.ai only)
- ✅ Error handling and logging

**Key features:**
```python
class GoogleOAuthProvider:
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"

    def __init__(self, client_id, client_secret, hd_domain="ciris.ai"):
        # hd_domain restricts to @ciris.ai

    async def get_authorization_url(state, redirect_uri) -> str:
        # Includes hd=ciris.ai parameter

    async def exchange_code_for_token(code, redirect_uri) -> OAuthToken:
        # Exchanges authorization code for access token

    async def get_user_info(access_token) -> OAuthUser:
        # Fetches user info and validates domain
```

### 4. **Admin Auth Service**

**File:** `app/services/admin_auth.py`

Complete authentication service:
- ✅ OAuth flow management (state, sessions)
- ✅ JWT token generation (HS256, 24h expiry)
- ✅ JWT token verification
- ✅ Admin user creation/update logic
- ✅ **Special handling for eric@ciris.ai** (default admin)
- ✅ Role assignment (admin vs viewer)
- ✅ Last login tracking

**Key features:**
```python
class AdminAuthService:
    async def initiate_oauth_flow(redirect_uri, callback_url) -> (state, auth_url):
        # Starts OAuth, returns Google auth URL

    async def handle_oauth_callback(code, state, db) -> dict:
        # Completes OAuth, creates/updates user, returns JWT

    async def _get_or_create_admin_user(db, oauth_user) -> AdminUser:
        # Special logic for eric@ciris.ai (always admin)
        # First user bootstrap
        # Other users default to viewer role

    def _create_jwt_token(admin_user) -> str:
        # JWT with sub, email, role, exp

    def verify_jwt_token(token) -> dict | None:
        # Validates JWT, returns payload
```

**Eric@ciris.ai bootstrap logic:**
```python
# From admin_auth.py line ~95
role = "admin" if oauth_user.email == "eric@ciris.ai" else "viewer"

if user_count == 0 and oauth_user.email == "eric@ciris.ai":
    logger.info("creating_first_admin_user")
    role = "admin"
```

### 5. **Updated Dependencies**

**File:** `requirements.txt`

Added OAuth dependencies:
```
google-auth==2.35.0
google-auth-oauthlib==1.2.1
google-auth-httplib2==0.2.0
```

---

## 🚧 Pending Implementation

### 1. **Database Migration**

**Action needed:** Create Alembic migration for updated `admin_users` table

**File to create:** `alembic/versions/2025_10_08_0003-update_admin_users_oauth.py`

**Migration SQL:**
```sql
-- Drop old columns
ALTER TABLE admin_users DROP COLUMN password_hash;
ALTER TABLE admin_users DROP COLUMN mfa_enabled;
ALTER TABLE admin_users DROP COLUMN mfa_secret;

-- Add new columns
ALTER TABLE admin_users ADD COLUMN google_id VARCHAR(255) UNIQUE;
ALTER TABLE admin_users ADD COLUMN picture_url VARCHAR(512);

-- Update role constraint (remove super_admin)
ALTER TABLE admin_users DROP CONSTRAINT ck_admin_users_role;
ALTER TABLE admin_users ADD CONSTRAINT ck_admin_users_role
    CHECK (role IN ('admin', 'viewer'));

-- Add domain constraint
ALTER TABLE admin_users ADD CONSTRAINT ck_admin_users_ciris_domain
    CHECK (email LIKE '%@ciris.ai');

-- Add index on google_id
CREATE INDEX idx_admin_users_google_id ON admin_users(google_id);
```

### 2. **Admin Auth Routes**

**File to create:** `app/api/admin_auth_routes.py`

**Routes needed:**
```python
GET  /admin/oauth/login       # Redirect to Google
GET  /admin/oauth/callback    # Handle Google callback
POST /admin/oauth/logout      # Clear JWT cookie
GET  /admin/oauth/user        # Get current user
```

**Implementation pattern:**
```python
router = APIRouter(prefix="/admin/oauth", tags=["admin-auth"])

@router.get("/login")
async def google_login(request: Request, redirect_uri: Optional[str] = None):
    state, auth_url = await auth_service.initiate_oauth_flow(...)
    return RedirectResponse(url=auth_url)

@router.get("/callback")
async def google_callback(code: str, state: str, db: AsyncSession = Depends(get_write_db)):
    result = await auth_service.handle_oauth_callback(code, state, db)
    response = RedirectResponse(url=f"{result['redirect_uri']}?token={result['access_token']}")
    response.set_cookie("admin_token", result["access_token"], httponly=True, secure=True)
    return response
```

### 3. **Admin Dependency**

**File to create:** `app/api/admin_dependencies.py`

**Dependency for protected admin routes:**
```python
async def get_current_admin(
    request: Request,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_write_db)
) -> AdminUser:
    """Get current authenticated admin user."""

    # Try Authorization header first
    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization.replace("Bearer ", "")

    # Try cookie if no header
    if not token:
        token = request.cookies.get("admin_token")

    if not token:
        raise HTTPException(401, "Not authenticated")

    # Verify JWT
    payload = auth_service.verify_jwt_token(token)
    if not payload:
        raise HTTPException(401, "Invalid token")

    # Get admin user
    user_id = UUID(payload["sub"])
    admin_user = await auth_service.get_admin_user_by_id(db, user_id)

    if not admin_user or not admin_user.is_active:
        raise HTTPException(403, "User not authorized")

    return admin_user

def require_admin_role(
    admin: AdminUser = Depends(get_current_admin)
) -> AdminUser:
    """Require admin role (not just viewer)."""
    if admin.role != "admin":
        raise HTTPException(403, "Admin role required")
    return admin
```

### 4. **Admin API Endpoints**

**File to create:** `app/api/admin_routes.py`

**Endpoints needed:**

**Users Management:**
```python
GET  /admin/users               # List all users (paginated)
GET  /admin/users/{account_id}  # Get user details
```

**API Keys Management:**
```python
GET    /admin/api-keys          # List all API keys
POST   /admin/api-keys          # Create new API key
DELETE /admin/api-keys/{id}     # Revoke API key
POST   /admin/api-keys/{id}/rotate  # Rotate API key
```

**Analytics:**
```python
GET /admin/analytics/overview   # Dashboard metrics
GET /admin/analytics/daily      # Daily aggregates
GET /admin/analytics/weekly     # Weekly aggregates
GET /admin/analytics/monthly    # Monthly aggregates
GET /admin/analytics/all-time   # All-time stats
```

**Configuration:**
```python
GET /admin/config/billing       # Get billing config
PUT /admin/config/billing       # Update billing config
GET /admin/config/providers     # Get provider configs
PUT /admin/config/providers/stripe  # Update Stripe config
```

### 5. **Update Admin UI**

**File to update:** `static/admin/admin.js`

**Changes needed:**

**Remove password login:**
```javascript
// DELETE: handleLogin(event) function (password-based)
// DELETE: login form with email/password/MFA inputs
```

**Add OAuth login:**
```javascript
function initiateGoogleLogin() {
    // Redirect to /admin/oauth/login
    const redirectUri = window.location.origin + '/admin';
    window.location.href = `/admin/oauth/login?redirect_uri=${encodeURIComponent(redirectUri)}`;
}

// On page load, check for token in URL
window.addEventListener('DOMContentLoaded', () => {
    const urlParams = new URLSearchParams(window.location.search);
    const token = urlParams.get('token');

    if (token) {
        // Store token
        localStorage.setItem('ciris_billing_admin_token', token);
        // Remove from URL
        window.history.replaceState({}, document.title, '/admin');
        // Show app
        showApp();
    } else if (checkAuth()) {
        showApp();
    } else {
        showLoginScreen();
    }
});
```

**Update login screen HTML:**
```html
<!-- REPLACE password form with: -->
<div class="text-center">
    <button onclick="initiateGoogleLogin()"
            class="w-full bg-blue-600 text-white py-3 rounded-lg hover:bg-blue-700">
        <i class="fab fa-google mr-2"></i>
        Sign in with Google
    </button>
    <p class="text-sm text-gray-500 mt-4">Only @ciris.ai emails are allowed</p>
</div>
```

### 6. **Configuration**

**File to create:** `.env` (add to existing)

```bash
# Google OAuth
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
ADMIN_JWT_SECRET=generate-with-openssl-rand-hex-32

# OAuth Settings
OAUTH_REDIRECT_URI=https://billing.ciris.ai/admin/oauth/callback
```

**Get Google OAuth credentials:**
1. Go to https://console.cloud.google.com/
2. Create new project or select existing
3. Enable Google+ API
4. Create OAuth 2.0 Client ID
5. Add authorized redirect URI: `https://billing.ciris.ai/admin/oauth/callback`
6. Copy Client ID and Client Secret

### 7. **Update Docker Compose**

**File:** `docker-compose.admin.yml`

Add environment variables:
```yaml
billing-api:
  environment:
    - GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}
    - GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET}
    - ADMIN_JWT_SECRET=${ADMIN_JWT_SECRET}
```

### 8. **Update Nginx Config**

**File:** `docker/nginx/admin-nginx.conf`

Update OAuth routes:
```nginx
# Admin OAuth routes
location /admin/oauth/ {
    proxy_pass http://billing_api/admin/oauth/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    # ... other headers
}
```

---

## 🔐 Security Features

### Domain Restriction

**Multiple layers:**
1. ✅ **Google OAuth `hd` parameter** - Restricts OAuth to @ciris.ai accounts at Google's auth screen
2. ✅ **Backend validation** - Rejects non-@ciris.ai emails in `OAuthUser.__post_init__`
3. ✅ **Database constraint** - `CHECK (email LIKE '%@ciris.ai')`

### Role-Based Access

**2 Roles:**
- **admin** - Full access (CRUD on users, keys, config)
- **viewer** - Read-only access (view dashboards, analytics)

**Special user:**
- **eric@ciris.ai** - Always created as `admin` role

**First user bootstrap:**
```python
if user_count == 0 and oauth_user.email == "eric@ciris.ai":
    role = "admin"  # Eric is first admin
else:
    role = "viewer"  # Others default to viewer
```

### JWT Security

- Algorithm: HS256
- Expiry: 24 hours
- Claims: `sub` (user_id), `email`, `role`, `iat`, `exp`
- HttpOnly cookie + Bearer token support

---

## 📋 Implementation Checklist

**Foundation (✅ Complete):**
- [x] Update AdminUser model
- [x] Create OAuth domain models
- [x] Implement GoogleOAuthProvider
- [x] Implement AdminAuthService
- [x] Add OAuth dependencies

**Integration (🚧 Pending):**
- [ ] Create database migration
- [ ] Implement admin auth routes
- [ ] Create admin dependencies
- [ ] Implement admin API endpoints (users, keys, analytics, config)
- [ ] Update admin UI for OAuth
- [ ] Configure Google OAuth credentials
- [ ] Update .env and docker-compose
- [ ] Test OAuth flow end-to-end
- [ ] Test eric@ciris.ai bootstrap
- [ ] Test domain restriction

**Testing (⏭️ Future):**
- [ ] Unit tests for OAuth provider
- [ ] Unit tests for auth service
- [ ] Integration tests for auth routes
- [ ] E2E test for full OAuth flow
- [ ] Test with non-@ciris.ai email (should reject)
- [ ] Test role permissions (admin vs viewer)

---

## 🚀 Quick Start (Once Complete)

### 1. Configure Google OAuth

```bash
# Get credentials from Google Cloud Console
# Add to .env
GOOGLE_CLIENT_ID=xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=xxx
ADMIN_JWT_SECRET=$(openssl rand -hex 32)
```

### 2. Run Migration

```bash
docker-compose -f docker-compose.admin.yml run --rm billing-api alembic upgrade head
```

### 3. Start Services

```bash
docker-compose -f docker-compose.admin.yml up -d
```

### 4. Login as Eric

1. Navigate to `https://billing.ciris.ai/admin`
2. Click "Sign in with Google"
3. Login with `eric@ciris.ai`
4. You'll be created as admin (first user bootstrap)

### 5. Other Users

- Any @ciris.ai user can login
- Default role: viewer
- Eric can promote to admin via admin UI

---

## 📚 References

**Adapted from:**
- CIRISManager OAuth implementation
- Files: `ciris_manager/api/google_oauth.py`, `auth_service.py`, `auth_routes.py`

**Documentation:**
- Google OAuth: https://developers.google.com/identity/protocols/oauth2
- JWT: https://jwt.io/
- FastAPI OAuth: https://fastapi.tiangolo.com/advanced/security/

---

## ⚠️ Important Notes

1. **Domain validation is critical** - Multiple layers ensure only @ciris.ai users
2. **Eric@ciris.ai is special** - Always admin role, first user bootstrap
3. **No password auth** - OAuth only, simpler and more secure
4. **JWT in cookie + header** - Supports both browser and API clients
5. **Migration required** - Must update database schema before deploying

---

## 🎯 Next Steps

1. Create database migration
2. Implement admin auth routes
3. Implement admin API endpoints
4. Update admin UI for OAuth
5. Configure Google OAuth credentials
6. Test end-to-end
7. Deploy to production

