# GitHub Deployment Guide — call-analytics

## 1️⃣ Create GitHub Repository

First, create a GitHub repository for the project:

### Option A: Via GitHub Web UI (Recommended)
1. Go to https://github.com/new
2. **Repository name**: `call-analytics`
3. **Description**: "Call quality analytics system with AI transcription and evaluation"
4. **Visibility**: `Private` (for security) or `Public` if sharing
5. **Do NOT initialize** with README, .gitignore, or license
6. Click **Create repository**

### Option B: Via GitHub CLI
```bash
gh repo create call-analytics --private --source=. --remote=origin --push
```

---

## 2️⃣ Push Code to GitHub

### Option A: HTTPS (Password Authentication)
```bash
# In /media/cosmos/2TB/neiro2/call-analytics
git remote add origin https://github.com/g.slanov/call-analytics.git
git branch -M main
git push -u origin main
```

When prompted, use:
- **Username**: `g.slanov` (or use your email)
- **Password**: Personal Access Token (not your actual password!)
  - Create at: https://github.com/settings/tokens
  - Scope needed: `repo` (full control of private repositories)

### Option B: SSH (Recommended for Servers)
```bash
# Generate SSH key (if not already present)
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa -N ""

# Display public key
cat ~/.ssh/id_rsa.pub
```

Then:
1. Go to https://github.com/settings/ssh/new
2. Paste the public key
3. Add key

Finally, push:
```bash
git remote add origin git@github.com:g.slanov/call-analytics.git
git branch -M main
git push -u origin main
```

---

## 3️⃣ Server Deployment

### First Deployment (from local machine)

Copy the deployment script to your server:
```bash
scp /tmp/deploy_call_analytics_github.sh root@23.94.143.122:/tmp/
```

SSH into server and run deployment:
```bash
ssh root@23.94.143.122
bash /tmp/deploy_call_analytics_github.sh
```

The script will:
- ✅ Install Docker & Docker Compose
- ✅ Clone the repository from GitHub
- ✅ Create directory structure
- ✅ Generate docker-compose.yml
- ✅ Create .env template

### Post-Deployment Setup

```bash
# SSH into server
ssh root@23.94.143.122

# Navigate to app
cd /app/call-analytics

# Edit .env with your real credentials
nano .env
# Set:
#   MANGO_FTP_HOST=your-ftp-server.com
#   MANGO_FTP_USER=your-username
#   MANGO_FTP_PASSWORD=your-password
#   OPENAI_API_KEY=sk-xxx (if you have it)

# Start services
docker-compose up -d

# Check status
docker-compose ps
docker-compose logs -f backend
```

---

## 4️⃣ GitHub Webhook (Auto-Deploy on Push)

To automatically deploy when you push to GitHub:

### On Server (Manual Setup)

Create `/usr/local/bin/call-analytics-webhook`:
```bash
#!/bin/bash
cd /app/call-analytics
git fetch origin
git reset --hard origin/main
docker-compose build --no-cache
docker-compose up -d
```

Make executable:
```bash
chmod +x /usr/local/bin/call-analytics-webhook
```

### Via GitHub Actions (Recommended)

Create `.github/workflows/deploy.yml` in your repository:

```yaml
name: Deploy to Production

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Deploy to server
        uses: appleboy/ssh-action@master
        with:
          host: ${{ secrets.SERVER_IP }}
          username: root
          key: ${{ secrets.SSH_PRIVATE_KEY }}
          script: |
            cd /app/call-analytics
            git pull origin main
            docker-compose build --no-cache
            docker-compose up -d
            docker-compose logs -f backend
```

Add secrets to GitHub:
1. Go to your repo → Settings → Secrets and variables → Actions
2. Add:
   - `SERVER_IP`: `23.94.143.122`
   - `SSH_PRIVATE_KEY`: Content of your `~/.ssh/id_rsa`

---

## 5️⃣ Update Process (After Initial Deployment)

Once deployed, updating is simple:

### Manual Update
```bash
ssh root@23.94.143.122
call-analytics-update
```

### Automatic Update (via GitHub Push)
```bash
# From your local machine
cd /media/cosmos/2TB/neiro2/call-analytics
git add -A
git commit -m "Fix: update feature or bugfix"
git push origin main
# ✅ Server automatically updates!
```

---

## 6️⃣ Monitoring & Logs

Check deployment status:
```bash
ssh root@23.94.143.122

# All containers
docker-compose ps

# Logs for specific service
docker-compose logs -f backend      # FastAPI backend
docker-compose logs -f frontend     # React frontend
docker-compose logs -f db           # PostgreSQL
docker-compose logs -f mango-sync   # МАНГО FTP sync

# Follow all logs
docker-compose logs -f
```

---

## 7️⃣ Troubleshooting

### Repository Not Found
```bash
# If SSH fails:
git remote set-url origin https://github.com/g.slanov/call-analytics.git

# Test connection:
ssh -T git@github.com
```

### Permission Denied
```bash
# Fix SSH permissions
chmod 700 ~/.ssh
chmod 600 ~/.ssh/id_rsa
chmod 644 ~/.ssh/id_rsa.pub
```

### Docker Build Fails
```bash
# Check Docker daemon
docker --version
systemctl status docker

# Rebuild without cache
docker-compose build --no-cache

# Clear all images
docker system prune -a
```

### Port Already in Use
If 3000, 8001, or 5432 are taken:
Edit `.env`:
```
FASTAPI_PORT=8002
FRONTEND_PORT=3001
# DB port in docker-compose.yml
```

---

## Summary

| Step | Command |
|------|---------|
| Create repo | https://github.com/new |
| Push code | `git push -u origin main` |
| Deploy | `bash /tmp/deploy_call_analytics_github.sh` |
| Configure | `nano /app/call-analytics/.env` |
| Start | `docker-compose up -d` |
| Update | `call-analytics-update` |
| Logs | `docker-compose logs -f` |

---

**Created**: 2026-02-25
**Server IP**: 23.94.143.122
**Repository**: https://github.com/g.slanov/call-analytics
