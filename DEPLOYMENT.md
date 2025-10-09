# CIRIS Billing - Deployment Guide

## Server Info
- IP: 149.28.120.73
- SSH: `ssh -i ~/.ssh/ciris_deploy root@149.28.120.73`
- Domain: billing.ciris.ai (needs DNS setup)

## Pre-Deployment Checklist

### 1. DNS Setup
- [ ] Point `billing.ciris.ai` A record to `149.28.120.73`
- [ ] Wait for propagation (verify: `dig billing.ciris.ai`)

### 2. Google OAuth
- [ ] Create OAuth client at https://console.cloud.google.com/
- [ ] Redirect URI: `https://billing.ciris.ai/admin/oauth/callback`
- [ ] Copy Client ID and Secret

### 3. Stripe (Optional)
- [ ] Get test API keys from https://dashboard.stripe.com/

## Quick Deploy

### 1. Prepare Server
```bash
ssh -i ~/.ssh/ciris_deploy root@149.28.120.73

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh && sh get-docker.sh
apt install docker-compose certbot -y
```

### 2. Upload Code
```bash
# From local machine
rsync -avz --exclude 'venv' --exclude '__pycache__' --exclude '.git' \
  -e "ssh -i ~/.ssh/ciris_deploy" \
  ./ root@149.28.120.73:/opt/ciris/billing/
```

### 3. Configure
```bash
cd /opt/ciris/billing
cp .env.example .env
nano .env  # Add real credentials
```

### 4. Get SSL Certificate
```bash
certbot certonly --standalone -d billing.ciris.ai --email eric@ciris.ai --agree-tos
```

### 5. Deploy
```bash
cd /opt/ciris/billing
docker-compose -f docker-compose.admin.yml up -d postgres
sleep 10
docker-compose -f docker-compose.admin.yml run --rm billing-api alembic upgrade head
docker-compose -f docker-compose.admin.yml up -d
```

### 6. Verify
```bash
docker-compose -f docker-compose.admin.yml ps
curl http://localhost/health
```

### 7. Login
- Go to https://billing.ciris.ai/admin
- Login with eric@ciris.ai
- Create API key for CIRISAgent

## Common Commands

```bash
# View logs
docker-compose -f docker-compose.admin.yml logs -f billing-api

# Restart
docker-compose -f docker-compose.admin.yml restart billing-api

# Database console
docker-compose -f docker-compose.admin.yml exec postgres psql -U ciris -d ciris_billing

# Backup
docker-compose -f docker-compose.admin.yml exec -T postgres \
  pg_dump -U ciris ciris_billing | gzip > backup_$(date +%Y%m%d).sql.gz
```
