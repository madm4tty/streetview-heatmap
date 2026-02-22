# Home Server Deployment Guide

This guide covers running Street View Heatmap on a Ubuntu LTS machine on your home LAN — for example, a spare PC, a NUC, or a Raspberry Pi 4. No domain name, Nginx, or SSL is needed. The app will be accessible to any device on your local network.

> **Not for public internet exposure.** This guide is intentionally simple for a trusted home network. If you want to expose the app to the internet, follow [DEPLOYMENT.md](./DEPLOYMENT.md) for Nginx and SSL setup instead.

---

## Pre-flight Checks

### 3.1 Pre-flight checks

Verify the basics before starting:

```bash
# Confirm Ubuntu version (guide targets 24.04 LTS)
lsb_release -a

# Ensure the system is fully up to date
sudo apt update && sudo apt upgrade -y

# Check available disk space (database grows over time — 20 GB free is comfortable)
df -h /

# Confirm Python 3.8+ is available
python3 --version
```

If Python 3.8+ is not installed:

```bash
sudo apt install -y python3 python3-pip python3-venv
```

---

## PostgreSQL + PostGIS Installation

### 3.2 Native installation (Ubuntu 24.04)

Ubuntu 24.04 LTS ships PostgreSQL 16 in its main package repositories. The package names differ slightly from the Ubuntu 22.04 guide in [DEPLOYMENT.md](./DEPLOYMENT.md):

```bash
# Install PostgreSQL 16 and the matching PostGIS extension
sudo apt install -y \
    postgresql \
    postgresql-contrib \
    postgis \
    postgresql-16-postgis-3

# Verify the service started
sudo systemctl status postgresql
```

> **22.04 vs 24.04 difference:** DEPLOYMENT.md uses `postgresql-14-postgis-3` (Ubuntu 22.04 ships PostgreSQL 14). On Ubuntu 24.04 the version is 16, so the package is `postgresql-16-postgis-3`.

### Create the database

```bash
sudo -u postgres psql
```

```sql
CREATE USER streetview WITH PASSWORD 'your_secure_database_password';
CREATE DATABASE streetview OWNER streetview;
\c streetview
CREATE EXTENSION postgis;
GRANT ALL PRIVILEGES ON DATABASE streetview TO streetview;
\q
```

Verify the connection:

```bash
psql -U streetview -h localhost -d streetview -c "SELECT PostGIS_Version();"
```

### Tune PostgreSQL for low RAM

For modest hardware (2–4 GB RAM), edit the PostgreSQL config to reduce memory usage:

```bash
sudo nano /etc/postgresql/16/main/postgresql.conf
```

Find and set the following values (they may already exist — update them in place):

```ini
shared_buffers = 256MB
work_mem = 4MB
effective_cache_size = 1GB
```

Restart PostgreSQL to apply:

```bash
sudo systemctl restart postgresql
```

---

## Application Setup

### 3.3 Clone, virtualenv, and install

Unlike [DEPLOYMENT.md](./DEPLOYMENT.md), there is no need to create a dedicated `heatmap` system user. Run as your own login account.

```bash
# Clone the repository into your home directory
git clone https://github.com/YOUR_USERNAME/streetview-heatmap.git
cd streetview-heatmap

# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn
```

### Create the .env file

```bash
nano .env
```

Add the following (replace placeholders with real values):

```bash
# Database connection
DATABASE_URL=postgresql://streetview:your_secure_database_password@localhost:5432/streetview

# Google Maps API key
GOOGLE_MAPS_API_KEY=your_google_maps_api_key

# API key for authenticated write endpoints (generate with: openssl rand -hex 32)
API_KEY=your_64_character_hex_string_here
```

Secure the file so only your user can read it:

```bash
chmod 600 .env
```

### Test the app manually

```bash
source venv/bin/activate
python3 run.py
```

In another terminal:

```bash
curl http://localhost:5001/api/health
```

Press `Ctrl+C` to stop once confirmed working.

---

## Systemd Service

### 3.4 Create a systemd unit file

This runs the app as your current user and loads the `.env` file automatically.

First, find your username:

```bash
whoami
# e.g. matt
```

Create the service file:

```bash
sudo nano /etc/systemd/system/heatmap.service
```

Paste the following, replacing `YOUR_USER` with your actual username:

```ini
[Unit]
Description=Street View Heatmap Flask Application
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=YOUR_USER
Group=YOUR_USER
WorkingDirectory=/home/YOUR_USER/streetview-heatmap
Environment="PATH=/home/YOUR_USER/streetview-heatmap/venv/bin"
EnvironmentFile=/home/YOUR_USER/streetview-heatmap/.env
ExecStart=/home/YOUR_USER/streetview-heatmap/venv/bin/gunicorn \
    --bind 0.0.0.0:5001 \
    --workers 2 \
    --threads 4 \
    --timeout 300 \
    --access-logfile - \
    --error-logfile - \
    "app:create_app()"
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=heatmap

[Install]
WantedBy=multi-user.target
```

> **LAN binding:** Note `--bind 0.0.0.0:5001` (not `127.0.0.1`) so the app is reachable from other devices on your network. This is intentional for a home LAN setup.

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable heatmap
sudo systemctl start heatmap
sudo systemctl status heatmap
```

---

## Local Network Access

### 3.5 Accessing the app on your LAN

Find your server's local IP address:

```bash
ip addr show | grep 'inet ' | grep -v '127.0.0.1'
# Look for something like: inet 192.168.1.42/24
```

The app will be accessible at `http://192.168.x.x:5001` from any device on your home network (phone, laptop, tablet, etc.).

No Nginx, no SSL, no firewall changes needed for a home LAN — your router already keeps the network private.

> **Security note:** This setup is intentionally simple and suitable only for a trusted home LAN. The app is not encrypted and the API key is sent in plain text. Do not expose port 5001 to the public internet without first following [DEPLOYMENT.md](./DEPLOYMENT.md) to set up Nginx and SSL.

---

## config.yaml Tuning

### 3.6 Conservative settings for modest hardware

Edit `config.yaml` in the project directory:

```bash
nano ~/streetview-heatmap/config.yaml
```

Recommended settings for a home server with limited RAM and CPU:

```yaml
update:
  batch_size: 30        # Reduced from default 50 — less memory pressure per job
  concurrency: 15       # Reduced from default 20 — fewer parallel API requests
  min_age_for_recheck_days: 90

scheduler:
  enabled: true
  interval_hours: 48    # Every 2 days instead of daily — gentler on the machine
```

Restart to apply:

```bash
sudo systemctl restart heatmap
```

---

## Storage Monitoring

### 3.7 Watching disk usage

The PostgreSQL database grows as more Street View metadata is collected. On a home server with a modest disk, keep an eye on it:

```bash
# Overall disk usage
df -h /

# Database size
sudo -u postgres psql -c "SELECT pg_size_pretty(pg_database_size('streetview'));"

# Size of individual tables
sudo -u postgres psql -d streetview -c "
SELECT
    relname AS table,
    pg_size_pretty(pg_total_relation_size(relid)) AS total_size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_total_relation_size(relid) DESC;
"
```

### Freeing space

If the database grows too large, you can reclaim space by vacuuming deleted rows:

```bash
# Vacuum and analyse all tables (safe to run while the app is running)
sudo -u postgres psql -d streetview -c "VACUUM ANALYSE;"

# More aggressive — reclaims disk space back to the OS (requires a brief table lock)
sudo -u postgres psql -d streetview -c "VACUUM FULL ANALYSE;"
```

To delete old low-priority tile data and start fresh on rural areas:

```bash
sudo -u postgres psql -d streetview
```

```sql
-- Delete metadata for low-priority tiles only (keeps city data intact)
DELETE FROM metadata WHERE tile_id IN (
    SELECT tile_id FROM metadata
    GROUP BY tile_id
    HAVING MAX(last_updated) < NOW() - INTERVAL '1 year'
);
VACUUM ANALYSE;
\q
```

---

## Deploying Updates

### 3.8 Using scripts/deploy.sh

The `scripts/deploy.sh` script automates the full update process: git pull, pip install, migrations, and service restart.

**Directly on the server:**

```bash
bash ~/streetview-heatmap/scripts/deploy.sh
```

**Remotely from another machine on your network:**

```bash
ssh your-server 'bash ~/streetview-heatmap/scripts/deploy.sh'
```

Replace `your-server` with the hostname or IP of your home server (e.g. `192.168.1.42`).

The script prints timestamped status messages at each step and exits with a clear error message if anything goes wrong.

### SSH key authentication (recommended)

To avoid typing your password every time you deploy remotely, set up SSH key authentication:

```bash
# On your local machine — generate a key pair if you don't already have one
ssh-keygen -t ed25519 -C "home-server"

# Copy your public key to the server
ssh-copy-id your-server
```

After this, `ssh your-server` will authenticate automatically without a password prompt.

---

## Startup on Boot

### 3.9 Verifying autostart

Both PostgreSQL and the heatmap service are configured to start automatically when the machine boots.

Confirm they are enabled:

```bash
# Check enabled state
sudo systemctl is-enabled postgresql
sudo systemctl is-enabled heatmap
# Both should print: enabled
```

Check they are currently running:

```bash
sudo systemctl status postgresql
sudo systemctl status heatmap
```

To test that everything comes up cleanly after a reboot:

```bash
sudo reboot
# Wait for the machine to come back, then from another machine:
curl http://192.168.x.x:5001/api/health
```

---

## Next Steps

- **Add initial data** — Trigger a first update job via the dashboard at `http://192.168.x.x:5001/dashboard` or the API
- **Monitor progress** — Watch logs with `sudo journalctl -u heatmap -f`
- **Daily operations** — See [QUICK_REFERENCE.md](./QUICK_REFERENCE.md) for common commands
