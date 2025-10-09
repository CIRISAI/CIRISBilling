# CIRIS Billing Admin UI - Deployment Guide

**Version:** 1.0
**Date:** 2025-10-08
**Domain:** billing.ciris.ai
**Status:** Ready for Deployment

---

## Overview

The CIRIS Billing Admin UI provides a web-based dashboard for managing:
- **Dashboard** - Revenue metrics, user statistics, activity feed
- **Users** - Search, filter, view all billing users
- **API Keys** - Create, rotate, revoke agent API keys
- **Analytics** - Daily, weekly, monthly, all-time reports
- **Configuration** - Billing settings, Stripe configuration

**Key Features:**
- ✅ Separate from CIRISManager (isolation of concerns)
- ✅ Static HTML/CSS/JS (no build step required)
- ✅ JWT authentication with MFA support
- ✅ Role-based access control
- ✅ HTTPS only with strict security headers
- ✅ Rate limiting on all endpoints
- ✅ Responsive design (mobile-friendly)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  billing.ciris.ai (Nginx)                                │
├─────────────────────────────────────────────────────────┤
│  /admin              →  Static HTML/CSS/JS               │
│  /admin/api/*        →  Backend API (auth, CRUD)         │
│  /v1/billing/*       →  Agent API (with X-API-Key)       │
│  /health             →  Health check                     │
│  /metrics            →  Prometheus metrics (internal)    │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│  Billing API (FastAPI)                                   │
│  - Admin authentication (JWT)                            │
│  - Admin CRUD endpoints                                  │
│  - Billing operations                                    │
└─────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────┐
│  PostgreSQL Database                                     │
│  - accounts, charges, credits                            │
│  - admin_users, api_keys                                 │
└─────────────────────────────────────────────────────────┘
```

**Isolation from CIRISManager:**
- Separate domain: `billing.ciris.ai` (not `manager.ciris.ai`)
- Separate docker-compose file
- Separate nginx instance
- No shared volumes or networks
- Independent SSL certificates
- Can scale/deploy independently

---

## Prerequisites

1. **Server Requirements:**
   - Ubuntu 22.04 LTS or later
   - 2+ CPU cores
   - 4GB+ RAM
   - 50GB+ disk space
   - Root or sudo access

2. **Software:**
   - Docker Engine 20.10+
   - Docker Compose 2.0+
   - Git

3. **Domain Setup:**
   - DNS A record: `billing.ciris.ai` → Your server IP
   - Firewall: Allow ports 80, 443

4. **Environment Variables:**
   - Stripe API keys (test + production)
   - PostgreSQL credentials
   - JWT secret key
   - Admin user credentials

---

## Deployment Steps

### Step 1: Clone Repository

```bash
cd /home/ciris
git clone https://github.com/cirisai/CIRISBilling.git
cd CIRISBilling
```

### Step 2: Configure Environment

Create `.env` file:

```bash
cat > .env <<'EOF'
# PostgreSQL Database
POSTGRES_USER=ciris
POSTGRES_PASSWORD=CHANGE_THIS_STRONG_PASSWORD
POSTGRES_DB=ciris_billing
DATABASE_URL=postgresql+asyncpg://ciris:CHANGE_THIS_STRONG_PASSWORD@postgres:5432/ciris_billing
DATABASE_URL_READ=postgresql+asyncpg://ciris:CHANGE_THIS_STRONG_PASSWORD@postgres:5432/ciris_billing

# Stripe Configuration
STRIPE_API_KEY=sk_live_YOUR_KEY_HERE
STRIPE_WEBHOOK_SECRET=whsec_YOUR_SECRET_HERE
STRIPE_PUBLISHABLE_KEY=pk_live_YOUR_KEY_HERE

# Admin Authentication
JWT_SECRET_KEY=CHANGE_THIS_TO_RANDOM_64_CHAR_STRING

# Observability
LOG_LEVEL=INFO
GRAFANA_PASSWORD=CHANGE_THIS_STRONG_PASSWORD

# Application
PYTHONUNBUFFERED=1
EOF
```

**⚠️ IMPORTANT:** Change all placeholder values!

Generate JWT secret:
```bash
openssl rand -hex 32
```

### Step 3: SSL Certificate Setup

Install Certbot:
```bash
sudo apt update
sudo apt install certbot -y
```

Obtain SSL certificate:
```bash
sudo certbot certonly --standalone \
  -d billing.ciris.ai \
  --non-interactive \
  --agree-tos \
  -m admin@ciris.ai
```

This creates certificates at:
- `/etc/letsencrypt/live/billing.ciris.ai/fullchain.pem`
- `/etc/letsencrypt/live/billing.ciris.ai/privkey.pem`

### Step 4: Run Database Migration

Start PostgreSQL only:
```bash
docker-compose -f docker-compose.admin.yml up -d postgres
```

Wait for healthy:
```bash
docker-compose -f docker-compose.admin.yml exec postgres pg_isready -U ciris
```

Run migrations:
```bash
docker-compose -f docker-compose.admin.yml run --rm billing-api alembic upgrade head
```

Expected output:
```
INFO  [alembic.runtime.migration] Running upgrade  -> 2025_01_01_0000, initial schema
INFO  [alembic.runtime.migration] Running upgrade 2025_01_01_0000 -> 2025_01_08_0001, add usage tracking
INFO  [alembic.runtime.migration] Running upgrade 2025_01_08_0001 -> 2025_10_08_0002, add admin system
```

### Step 5: Create First Admin User

Create bootstrap script:

```bash
cat > scripts/create_admin.py <<'PYTHON'
import asyncio
import sys
from argon2 import PasswordHasher
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

sys.path.insert(0, '/app')

from app.db.models import AdminUser

DATABASE_URL = os.getenv('DATABASE_URL').replace('asyncpg', 'psycopg2')

async def create_admin():
    engine = create_engine(DATABASE_URL.replace('+asyncpg', ''))
    Session = sessionmaker(bind=engine)
    session = Session()

    email = input("Admin email: ")
    password = input("Admin password: ")
    full_name = input("Full name: ")

    ph = PasswordHasher()
    password_hash = ph.hash(password)

    admin = AdminUser(
        id=uuid4(),
        email=email,
        password_hash=password_hash,
        full_name=full_name,
        role="super_admin",
        is_active=True,
        mfa_enabled=False
    )

    session.add(admin)
    session.commit()

    print(f"\n✅ Admin user created successfully!")
    print(f"   Email: {admin.email}")
    print(f"   User ID: {admin.id}")
    print(f"   Role: {admin.role}")
    print(f"\nYou can now login at https://billing.ciris.ai/admin")

if __name__ == "__main__":
    asyncio.run(create_admin())
PYTHON

chmod +x scripts/create_admin.py
```

Run it:
```bash
docker-compose -f docker-compose.admin.yml run --rm billing-api python scripts/create_admin.py
```

Enter admin credentials when prompted.

### Step 6: Start All Services

```bash
docker-compose -f docker-compose.admin.yml up -d
```

Verify all services are healthy:
```bash
docker-compose -f docker-compose.admin.yml ps
```

Expected output:
```
NAME                           STATUS         PORTS
ciris-billing-admin-nginx      Up (healthy)   0.0.0.0:80->80/tcp, 0.0.0.0:443->443/tcp
ciris-billing-api              Up (healthy)   127.0.0.1:8000->8000/tcp
ciris-billing-postgres         Up (healthy)   127.0.0.1:5432->5432/tcp
ciris-billing-grafana          Up             127.0.0.1:3000->3000/tcp
ciris-billing-jaeger           Up             127.0.0.1:4317-4318->4317-4318/tcp, 127.0.0.1:16686->16686/tcp
ciris-billing-prometheus       Up             127.0.0.1:9090->9090/tcp
```

### Step 7: Test Admin Login

Open browser:
```
https://billing.ciris.ai/admin
```

Login with admin credentials created in Step 5.

You should see the admin dashboard!

### Step 8: Generate First API Key

1. Click "API Keys" tab
2. Click "Create API Key"
3. Fill in:
   - Name: "Production Agent"
   - Description: "Main production CIRIS Agent"
   - Environment: "Live"
   - Expires In: 90 (days)
4. Click "Create"
5. **COPY THE API KEY** (shown only once!)
6. Save it to your CIRISAgent `.env`:
   ```bash
   BILLING_API_KEY=cbk_live_abc123...
   ```

---

## Post-Deployment

### SSL Certificate Auto-Renewal

Create renewal cron job:
```bash
sudo crontab -e
```

Add:
```
0 0 * * * certbot renew --quiet && docker-compose -f /home/ciris/CIRISBilling/docker-compose.admin.yml restart nginx-admin
```

### Monitoring Setup

**Prometheus:**
- URL: `http://your-server-ip:9090`
- Metrics: API latency, request rates, error rates

**Grafana:**
- URL: `http://your-server-ip:3000`
- Login: admin / (password from .env)
- Pre-configured dashboards for billing metrics

**Jaeger:**
- URL: `http://your-server-ip:16686`
- Distributed tracing for debugging

### Firewall Configuration

Allow only necessary ports:
```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 22/tcp
sudo ufw enable
```

### Backup Setup

Create backup script:
```bash
cat > scripts/backup.sh <<'BASH'
#!/bin/bash
BACKUP_DIR="/backups/ciris-billing"
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Backup database
docker-compose -f docker-compose.admin.yml exec -T postgres pg_dump -U ciris ciris_billing | gzip > $BACKUP_DIR/db_$DATE.sql.gz

# Backup .env
cp .env $BACKUP_DIR/env_$DATE

# Keep last 30 days
find $BACKUP_DIR -type f -mtime +30 -delete

echo "Backup completed: $BACKUP_DIR/db_$DATE.sql.gz"
BASH

chmod +x scripts/backup.sh
```

Run daily:
```bash
sudo crontab -e
```

Add:
```
0 2 * * * /home/ciris/CIRISBilling/scripts/backup.sh
```

---

## Updating

### Update Code

```bash
cd /home/ciris/CIRISBilling
git pull origin main
```

### Update Database Schema

```bash
docker-compose -f docker-compose.admin.yml run --rm billing-api alembic upgrade head
```

### Restart Services

```bash
docker-compose -f docker-compose.admin.yml restart
```

---

## Troubleshooting

### Admin UI Not Loading

Check nginx logs:
```bash
docker-compose -f docker-compose.admin.yml logs nginx-admin
```

Check static files are mounted:
```bash
docker-compose -f docker-compose.admin.yml exec nginx-admin ls -la /usr/share/nginx/html/admin
```

### Cannot Login

Check API logs:
```bash
docker-compose -f docker-compose.admin.yml logs billing-api
```

Verify admin user exists:
```bash
docker-compose -f docker-compose.admin.yml exec postgres psql -U ciris -d ciris_billing -c "SELECT email, role, is_active FROM admin_users;"
```

### SSL Certificate Issues

Check certificate:
```bash
sudo certbot certificates
```

Renew manually:
```bash
sudo certbot renew --force-renewal
docker-compose -f docker-compose.admin.yml restart nginx-admin
```

### Database Connection Failed

Check database:
```bash
docker-compose -f docker-compose.admin.yml exec postgres pg_isready -U ciris
```

Check connection string in .env matches credentials.

### 502 Bad Gateway

API not healthy:
```bash
docker-compose -f docker-compose.admin.yml logs billing-api
curl http://localhost:8000/health
```

---

## Security Checklist

- [ ] Changed all default passwords in `.env`
- [ ] Generated strong JWT secret key (64+ chars)
- [ ] SSL certificate installed and auto-renewing
- [ ] Firewall configured (only 80, 443, 22)
- [ ] Admin user created with strong password
- [ ] MFA enabled for super_admin (optional)
- [ ] Rate limiting enabled (nginx config)
- [ ] Security headers configured (CSP, HSTS, etc.)
- [ ] Database backups scheduled
- [ ] Monitoring configured (Prometheus, Grafana)
- [ ] Log rotation configured
- [ ] Test admin login works
- [ ] Test API key creation works
- [ ] Test agent can access billing API with key

---

## Performance Tuning

### For High Traffic

Edit `docker-compose.admin.yml`:

```yaml
billing-api:
  deploy:
    replicas: 3  # Run 3 API instances
    resources:
      limits:
        cpus: '2'
        memory: 2G

postgres:
  command: >
    postgres
    -c max_connections=200
    -c shared_buffers=1GB
    -c effective_cache_size=3GB
```

### Nginx Worker Processes

Edit `docker/nginx/admin-nginx.conf`:

```nginx
worker_processes 4;  # Match CPU cores
```

---

## Maintenance Commands

**View logs:**
```bash
docker-compose -f docker-compose.admin.yml logs -f billing-api
docker-compose -f docker-compose.admin.yml logs -f nginx-admin
```

**Restart service:**
```bash
docker-compose -f docker-compose.admin.yml restart billing-api
```

**Database shell:**
```bash
docker-compose -f docker-compose.admin.yml exec postgres psql -U ciris -d ciris_billing
```

**API shell:**
```bash
docker-compose -f docker-compose.admin.yml exec billing-api python
```

**Stop all:**
```bash
docker-compose -f docker-compose.admin.yml down
```

**Remove everything (DANGER):**
```bash
docker-compose -f docker-compose.admin.yml down -v
```

---

## Support

- **Documentation:** See `ADMIN_UI.md` for architecture
- **API Reference:** See `INTEGRATION.md` for API details
- **Quickstart:** See `API_KEY_QUICKSTART.md` for API key setup

---

## Next Steps

1. ✅ Admin UI deployed and accessible
2. ⏭️ Update CIRISAgent to use new billing API
3. ⏭️ Configure Stripe webhooks
4. ⏭️ Set up production monitoring alerts
5. ⏭️ Train team on admin UI usage

