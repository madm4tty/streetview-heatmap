# GCP Deployment Guide

This guide covers deploying the Street View Heatmap on Google Cloud Platform using a single VM running Docker Compose. Two options are available:

| Option | Machine type | Cost | RAM | Notes |
|--------|-------------|------|-----|-------|
| A | e2-micro | **Free** | 1 GB | Always-free tier — US regions only |
| B | e2-small | ~$13/month | 2 GB | More comfortable for tile processing |

Start with Option A. If tile processing causes OOM crashes, resize to e2-small.

---

## Prerequisites

- Google Cloud account with billing enabled (required even for free tier)
- `gcloud` CLI installed and authenticated: `gcloud auth login`
- A GCP project created: `gcloud projects create YOUR_PROJECT_ID`
- Project set as default: `gcloud config set project YOUR_PROJECT_ID`

---

## 1. Create the VM

### Option A — Free e2-micro (US regions only)

```bash
gcloud compute instances create streetview-heatmap \
  --zone=us-east1-b \
  --machine-type=e2-micro \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --tags=http-server,https-server
```

### Option B — e2-small (~$13/month, any region)

```bash
gcloud compute instances create streetview-heatmap \
  --zone=europe-west2-a \
  --machine-type=e2-small \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=30GB \
  --tags=http-server,https-server
```

## 2. Open firewall ports

```bash
gcloud compute firewall-rules create allow-streetview \
  --allow tcp:80,tcp:443,tcp:5001 \
  --target-tags http-server,https-server \
  --description "Allow web traffic to Street View Heatmap"
```

## 3. SSH into the VM

```bash
gcloud compute ssh streetview-heatmap --zone=us-east1-b
```

## 4. Install Docker (run on the VM)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
newgrp docker
```

Verify Docker is working:

```bash
docker run hello-world
```

## 5. Clone the repo

```bash
git clone git@github.com:YOUR_USERNAME/streetview-heatmap.git
cd streetview-heatmap
```

If using HTTPS instead:

```bash
git clone https://github.com/YOUR_USERNAME/streetview-heatmap.git
cd streetview-heatmap
```

## 6. Create the .env file

```bash
cat > .env << 'EOF'
GOOGLE_MAPS_API_KEY=your_google_maps_api_key_here
API_KEY=choose_a_strong_random_api_key
DB_PASSWORD=choose_a_strong_database_password
EOF
```

**Never commit this file.** It is already in `.gitignore`.

## 7. (Option A only) Tune config.yaml for 1 GB RAM

Edit `config.yaml` to use conservative processing limits:

```yaml
update:
  batch_size: 20      # down from 150
  concurrency: 20     # down from 80
```

This prevents OOM during tile processing on the 1 GB VM. Skip this step for Option B.

## 8. Build and start

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

The first build takes a few minutes. Subsequent starts are fast.

## 9. Run database migrations

```bash
docker compose -f docker-compose.prod.yml exec app \
  python3 migrations/001_create_job_status.py
```

---

## Verification

```bash
# Check both containers are running
docker compose -f docker-compose.prod.yml ps

# Get the VM's external IP
curl -s ifconfig.me

# Check the health endpoint
curl http://EXTERNAL_IP:5001/api/health

# Trigger a small test job (replace YOUR_API_KEY)
curl -X POST http://EXTERNAL_IP:5001/api/update/trigger \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"priority": "high", "tile_limit": 2}'

# Watch live logs
docker compose -f docker-compose.prod.yml logs -f app
```

---

## Day-to-day operations

```bash
# Stop
docker compose -f docker-compose.prod.yml down

# Restart app only (after a code change)
docker compose -f docker-compose.prod.yml up -d --build app

# Pull latest code and redeploy
git pull
docker compose -f docker-compose.prod.yml up -d --build

# View postgres logs
docker compose -f docker-compose.prod.yml logs postgres

# Connect to the database
docker compose -f docker-compose.prod.yml exec postgres \
  psql -U streetview -d streetview
```

## Backing up the database

```bash
# Dump to a file
docker compose -f docker-compose.prod.yml exec postgres \
  pg_dump -U streetview streetview > backup_$(date +%Y%m%d).sql

# Restore from a dump
docker compose -f docker-compose.prod.yml exec -T postgres \
  psql -U streetview streetview < backup_20260101.sql
```

---

## Resizing from e2-micro to e2-small (Option A → B)

If you start seeing OOM kills on the e2-micro:

```bash
# Stop the VM first
gcloud compute instances stop streetview-heatmap --zone=us-east1-b

# Change machine type
gcloud compute instances set-machine-type streetview-heatmap \
  --zone=us-east1-b \
  --machine-type=e2-small

# Start it again
gcloud compute instances start streetview-heatmap --zone=us-east1-b
```

After resizing, you can remove the memory-tuning `command:` block from `docker-compose.prod.yml` and revert `config.yaml` to larger batch sizes.
