# CIRIS Billing Security Implementation Plan

**Goal:** Move secrets to Docker secrets with local backups, make it repeatable

---

## Phase 1: Docker Secrets (Start Here)

### What We're Changing
- Move DATABASE_URL from environment → Docker secret file
- Move GOOGLE_CLIENT_SECRET from environment → Docker secret file
- Move SECRET_KEY from environment → Docker secret file
- Generate encryption key for future Stripe key encryption

### What Gets Committed to Git
- ✅ `scripts/setup-secrets.sh` - Script to create secret files from environment
- ✅ Updated `.gitignore` - Exclude secrets directory
- ✅ Updated `docker-compose.admin.yml` - Use Docker secrets
- ✅ `SECURITY_IMPLEMENTATION_PLAN.md` - This document
- ❌ `secrets/` directory - NEVER committed (contains actual credentials)

### What Stays on Server Only
- `/opt/ciris/billing/secrets/` - Actual secret files
- `/opt/ciris/billing/secrets-backup/` - Local backups for rollback
- `docker-compose.admin.yml.backup-*` - Configuration backups

---

## Implementation Steps

### Step 1: Local Development - Create Setup Script

Create `scripts/setup-secrets.sh`:
- Reads current environment variables
- Creates secrets directory
- Writes secret files with correct permissions
- Creates backup of current credentials
- Validates all secrets exist

### Step 2: Local Development - Update Configuration

Update `docker-compose.admin.yml`:
- Add secrets definitions at top level
- Mount secrets in billing-api service
- Update command to read from secret files
- Remove secrets from environment section

Update `.gitignore`:
- Add `secrets/`
- Add `secrets-backup/`
- Add `*.backup-*`

### Step 3: Commit and Push

```bash
git add scripts/setup-secrets.sh
git add docker-compose.admin.yml
git add .gitignore
git commit -m "Add Docker secrets support for credential management"
git push
```

### Step 4: Server Deployment

```bash
# Backup current config
cp docker-compose.admin.yml docker-compose.admin.yml.backup-$(date +%Y%m%d)

# Pull latest code
git pull

# Run setup script (creates secrets from current env)
./scripts/setup-secrets.sh

# Restart with new config
docker-compose -f docker-compose.admin.yml down
docker-compose -f docker-compose.admin.yml up -d

# Verify
./scripts/verify-secrets.sh
```

### Step 5: Verification

- API health check works
- OAuth login works
- Billing endpoints work
- Secrets NOT visible in `docker exec ... env`
- Secrets ARE readable from `/run/secrets/`

---

## Rollback Plan

If anything breaks:

```bash
# Restore old docker-compose
cp docker-compose.admin.yml.backup-YYYYMMDD docker-compose.admin.yml

# Restart
docker-compose -f docker-compose.admin.yml down
docker-compose -f docker-compose.admin.yml up -d
```

Credentials are backed up in `secrets-backup/current-credentials.txt`

---

## Phase 2: Database Encryption (Later)

After Phase 1 is stable and working for a few days:

1. Add cryptography to requirements.txt
2. Create encryption service
3. Create migration script for Stripe keys
4. Deploy and test

---

## Phase 3: Remove Port 8000 (Later)

After Phase 1 and 2 are stable:

1. Remove ports section from billing-api in docker-compose
2. Test nginx still works
3. Verify port 8000 not accessible externally

---

## Current Status

- [x] Secrets directories created on server
- [x] Current credentials backed up
- [x] Secret files created
- [ ] Setup script written and committed
- [ ] .gitignore updated
- [ ] docker-compose updated
- [ ] Changes committed to git
- [ ] Deployed and tested
- [ ] Verified working
