# Street View Heatmap - Production Deployment Guide

This guide walks you through deploying the Street View Heatmap application on a VPS with a **simplified architecture**: public frontend only, configuration via SSH + text editor.

## Architecture Overview

```
┌─────────────────┐      ┌─────────────────┐      ┌─────────────────┐
│  Public Internet │──────│     Nginx       │──────│   Flask App     │
│                 │ :443  │  (reverse proxy)│ :5001│   (Gunicorn)    │
└─────────────────┘      └─────────────────┘      └────────┬────────┘
                                                           │
                                                  ┌────────▼────────┐
                                                  │   PostgreSQL    │
                                                  │   + PostGIS     │
                                                  │     :5432       │
                                                  └─────────────────┘
```

**Key Design Decisions:**

- **No web admin interface** - Configuration changes via SSH + editing `config.yaml`
- **Single domain** - No subdomains needed, just `yourdomain.com`
- **Blocked /config page** - Nginx returns 404, reducing attack surface
- **API key for writes** - POST endpoints require `X-API-Key` header

This approach is simpler and more secure for single-admin deployments. If you're SSH'd into the server for maintenance anyway, editing a config file is straightforward.

---

## Prerequisites

- **VPS**: 2GB RAM minimum (4GB recommended for larger datasets)
- **OS**: Ubuntu 22.04 LTS
- **Domain**: One domain name pointed to your server
- **Access**: Root or sudo privileges
- **Skills**: Basic Linux command line familiarity

---

## Part 1: Server Setup

### 1.1 Initial System Configuration

```bash
# Update system packages
sudo apt update && sudo apt upgrade -y

# Set timezone (optional, adjust to your preference)
sudo timedatectl set-timezone UTC

# Install essential tools
sudo apt install -y curl wget git unzip htop
```

### 1.2 Create Application User

```bash
# Create dedicated user for the application
sudo adduser heatmap --gecos "" --disabled-password

# Add to sudo group for service management
sudo usermod -aG sudo heatmap

# Set a password (you'll need this for sudo)
sudo passwd heatmap
```

### 1.3 Configure Firewall

```bash
# Allow SSH (important: do this first!)
sudo ufw allow OpenSSH

# Allow HTTP and HTTPS
sudo ufw allow 'Nginx Full'

# Enable firewall
sudo ufw enable

# Verify status
sudo ufw status
```

### 1.4 Install System Dependencies

```bash
# Python and build tools
sudo apt install -y python3 python3-pip python3-venv python3-dev build-essential

# PostgreSQL with PostGIS
sudo apt install -y postgresql postgresql-contrib postgis postgresql-14-postgis-3

# Nginx and Certbot
sudo apt install -y nginx certbot python3-certbot-nginx
```

---

## Part 2: Database Setup

### 2.1 Create Database and User

```bash
# Access PostgreSQL as superuser
sudo -u postgres psql
```

Run these SQL commands:

```sql
-- Create database user (use a strong password!)
CREATE USER streetview WITH PASSWORD 'your_secure_database_password';

-- Create database
CREATE DATABASE streetview OWNER streetview;

-- Connect to the new database
\c streetview

-- Enable PostGIS extension
CREATE EXTENSION postgis;

-- Grant privileges
GRANT ALL PRIVILEGES ON DATABASE streetview TO streetview;

-- Exit
\q
```

### 2.2 Configure PostgreSQL Authentication

```bash
# Edit pg_hba.conf to allow password authentication
sudo nano /etc/postgresql/14/main/pg_hba.conf
```

Find the line for local connections and ensure it uses `md5` or `scram-sha-256`:

```
# IPv4 local connections:
host    all             all             127.0.0.1/32            scram-sha-256
```

Restart PostgreSQL:

```bash
sudo systemctl restart postgresql
```

### 2.3 Verify Database Connection

```bash
# Test connection
psql -U streetview -h localhost -d streetview -c "SELECT PostGIS_Version();"
```

You should see the PostGIS version output.

---

## Part 3: Application Setup

### 3.1 Clone Repository

```bash
# Switch to heatmap user
sudo su - heatmap

# Clone the repository
git clone https://github.com/madm4tty/streetview-heatmap.git
cd streetview-heatmap
```

### 3.2 Create Virtual Environment

```bash
# Create and activate virtual environment
python3 -m venv venv
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt

# Install production WSGI server
pip install gunicorn
```

### 3.3 Configure Environment Variables

```bash
# Create .env file
nano .env
```

Add the following (replace placeholders with real values):

```bash
# Database connection
DATABASE_URL=postgresql://streetview:your_secure_database_password@localhost:5432/streetview

# Google Maps API key (for Street View Metadata API)
GOOGLE_MAPS_API_KEY=your_google_maps_api_key

# API key for authenticated endpoints (generate with: openssl rand -hex 32)
API_KEY=your_64_character_hex_string_here
```

Secure the file:

```bash
chmod 600 .env
```

### 3.4 Initialize Database

```bash
# Ensure virtual environment is active
source venv/bin/activate

# Initialize database tables
python -c "import database; database.init_db()"
```

### 3.5 Test Application

```bash
# Start Flask development server
python run.py
```

You should see:

```
 * Running on http://0.0.0.0:5001
```

Test in another terminal:

```bash
curl http://localhost:5001/api/health
```

Press `Ctrl+C` to stop the test server.

---

## Part 4: Systemd Service

### 4.1 Create Service File

```bash
# Exit back to your admin user
exit

# Create systemd service file
sudo nano /etc/systemd/system/heatmap.service
```

Add this content:

```ini
[Unit]
Description=Street View Heatmap Flask Application
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=simple
User=heatmap
Group=heatmap
WorkingDirectory=/home/heatmap/streetview-heatmap
Environment="PATH=/home/heatmap/streetview-heatmap/venv/bin"
EnvironmentFile=/home/heatmap/streetview-heatmap/.env
ExecStart=/home/heatmap/streetview-heatmap/venv/bin/gunicorn \
    --bind 127.0.0.1:5001 \
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

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/home/heatmap/streetview-heatmap/logs

[Install]
WantedBy=multi-user.target
```

### 4.2 Create Logs Directory

```bash
sudo mkdir -p /home/heatmap/streetview-heatmap/logs
sudo chown heatmap:heatmap /home/heatmap/streetview-heatmap/logs
```

### 4.3 Enable and Start Service

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable service to start on boot
sudo systemctl enable heatmap

# Start the service
sudo systemctl start heatmap

# Check status
sudo systemctl status heatmap
```

### 4.4 Verify Service

```bash
# Check if it's listening
curl http://localhost:5001/api/health
```

---

## Part 5: Nginx Configuration

### 5.1 Copy Configuration

```bash
# Copy the nginx config
sudo cp /home/heatmap/streetview-heatmap/nginx/heatmap.conf /etc/nginx/sites-available/heatmap
```

### 5.2 Edit Domain Name

```bash
# Edit the config and replace 'yourdomain.com' with your actual domain
sudo nano /etc/nginx/sites-available/heatmap
```

Use find/replace in nano: `Ctrl+\`, enter `yourdomain.com`, then your domain, then `A` to replace all.

### 5.3 Enable Site

```bash
# Create symlink to enable site
sudo ln -s /etc/nginx/sites-available/heatmap /etc/nginx/sites-enabled/

# Remove default site
sudo rm -f /etc/nginx/sites-enabled/default

# Test configuration
sudo nginx -t

# Reload nginx
sudo systemctl reload nginx
```

---

## Part 6: DNS Configuration

Add a single A record in your domain registrar's DNS settings:

| Type | Name | Value | TTL |
|------|------|-------|-----|
| A | @ | your_server_ip | 300 |

Wait for DNS propagation (can take up to 48 hours, usually much faster).

Verify DNS:

```bash
dig yourdomain.com +short
```

Should return your server's IP address.

---

## Part 7: SSL Certificate

### 7.1 Obtain Certificate

```bash
# Run certbot (will automatically configure nginx)
sudo certbot --nginx -d yourdomain.com
```

Follow the prompts:
1. Enter email for renewal notices
2. Agree to terms
3. Choose whether to share email with EFF
4. Select option 2 to redirect HTTP to HTTPS

### 7.2 Verify Auto-Renewal

```bash
# Test renewal process
sudo certbot renew --dry-run
```

Certbot automatically creates a cron job for renewal.

---

## Part 8: Verification

### 8.1 Test Public Frontend

```bash
# Health check
curl -I https://yourdomain.com

# API health
curl https://yourdomain.com/api/health | jq

# System status
curl https://yourdomain.com/api/status | jq
```

### 8.2 Verify Blocked Routes

```bash
# Should return 404
curl -I https://yourdomain.com/config
```

Expected output includes: `HTTP/2 404`

### 8.3 Test API Authentication

```bash
# POST without API key - should get 401
curl -X POST https://yourdomain.com/api/update/trigger \
  -H "Content-Type: application/json"

# POST with API key - should work
curl -X POST https://yourdomain.com/api/update/trigger \
  -H "X-API-Key: your_api_key" \
  -H "Content-Type: application/json" \
  -d '{"priority": "high", "tile_limit": 5}'
```

---

## Part 9: Configuration Management

This deployment uses **direct file editing** instead of a web interface. Here's how to manage configuration:

### 9.1 Editing Configuration

```bash
# SSH into server
ssh heatmap@yourdomain.com

# Navigate to project
cd ~/streetview-heatmap

# Edit configuration
nano config.yaml

# Make your changes, save (Ctrl+X, Y, Enter)

# Restart application to apply
sudo systemctl restart heatmap

# Verify it's running
sudo systemctl status heatmap
```

### 9.2 Configuration Options

**Scheduler Settings** (`scheduler` section):

```yaml
scheduler:
  enabled: true           # Enable/disable automatic updates
  interval_hours: 24      # How often to run updates (24 = daily)
```

**Update Settings** (`update` section):

```yaml
update:
  batch_size: 50          # Tiles to process per batch
  concurrency: 20         # Parallel API requests
  min_age_for_recheck_days: 90    # Days before re-checking a tile
  overpass_delay_seconds: 2       # Delay between Overpass API calls
  samples_per_road: 5             # Street View samples per road segment
  adaptive_sampling: true         # Adjust sampling based on road type
```

### 9.3 Common Configuration Changes

**Change update frequency to weekly:**

```yaml
scheduler:
  interval_hours: 168     # 7 days * 24 hours
```

**Reduce resource usage (for smaller VPS):**

```yaml
update:
  batch_size: 25
  concurrency: 10
```

**Increase throughput (for larger VPS):**

```yaml
update:
  batch_size: 100
  concurrency: 30
```

### 9.4 Verify Configuration Changes

After restarting the service:

```bash
# Check current configuration via API
curl http://localhost:5001/api/config | jq
```

---

## Part 10: Maintenance

### 10.1 View Logs

```bash
# Application logs (real-time)
sudo journalctl -u heatmap -f

# Last 100 log entries
sudo journalctl -u heatmap -n 100

# Logs from today
sudo journalctl -u heatmap --since today

# Nginx access logs
sudo tail -f /var/log/nginx/heatmap_access.log

# Nginx error logs
sudo tail -f /var/log/nginx/heatmap_error.log
```

### 10.2 Update Application

```bash
# SSH into server as heatmap user
ssh heatmap@yourdomain.com

# Pull latest code
cd ~/streetview-heatmap
git pull

# Activate virtual environment
source venv/bin/activate

# Update dependencies
pip install -r requirements.txt

# Run database migrations if any
python -c "import database; database.init_db()"

# Restart service
sudo systemctl restart heatmap

# Verify
sudo systemctl status heatmap
```

### 10.3 Database Backup

```bash
# Create backup
pg_dump -U streetview -h localhost streetview | gzip > ~/backup_$(date +%Y%m%d_%H%M%S).sql.gz

# Restore from backup
gunzip < backup_file.sql.gz | psql -U streetview -h localhost streetview
```

Set up automated daily backups:

```bash
# Edit crontab
crontab -e

# Add this line for daily backups at 3 AM
0 3 * * * pg_dump -U streetview -h localhost streetview | gzip > /home/heatmap/backups/streetview_$(date +\%Y\%m\%d).sql.gz
```

### 10.4 Monitor Resources

```bash
# Interactive process viewer
htop

# Disk usage
df -h

# Database size
sudo -u postgres psql -c "SELECT pg_size_pretty(pg_database_size('streetview'));"

# Memory usage
free -h
```

---

## Security Checklist

Before going live, verify:

- [ ] Strong PostgreSQL password (20+ characters, mixed case, numbers, symbols)
- [ ] Strong API_KEY generated (`openssl rand -hex 32`)
- [ ] `.env` file permissions set to 600 (`chmod 600 .env`)
- [ ] Firewall enabled with only necessary ports (22, 80, 443)
- [ ] SSH key authentication enabled (disable password auth)
- [ ] SSL certificate obtained and working
- [ ] HTTP redirects to HTTPS
- [ ] `/config` route returns 404
- [ ] POST endpoints require API key
- [ ] Database backups configured
- [ ] Fail2ban installed (optional but recommended)

### Optional: Install Fail2ban

```bash
sudo apt install -y fail2ban

# Create local config
sudo cp /etc/fail2ban/jail.conf /etc/fail2ban/jail.local

# Enable and start
sudo systemctl enable fail2ban
sudo systemctl start fail2ban
```

### Optional: Automatic Security Updates

```bash
sudo apt install -y unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades
```

---

## Troubleshooting

### Application Won't Start

```bash
# Check service status
sudo systemctl status heatmap

# View recent logs
sudo journalctl -u heatmap -n 50

# Test manually
cd /home/heatmap/streetview-heatmap
source venv/bin/activate
python run.py  # Look for errors
```

### Nginx 502 Bad Gateway

```bash
# Check if Flask is running
curl http://localhost:5001/api/health

# If not running, restart it
sudo systemctl restart heatmap

# Check nginx config
sudo nginx -t
```

### Can't Access Site

```bash
# Check DNS
dig yourdomain.com +short

# Check firewall
sudo ufw status

# Check nginx
sudo systemctl status nginx
sudo nginx -t

# Check if ports are listening
sudo netstat -tulpn | grep -E ':(80|443|5001)'
```

### Database Connection Issues

```bash
# Check PostgreSQL status
sudo systemctl status postgresql

# Test connection
psql -U streetview -h localhost -d streetview -c "SELECT 1;"

# Check logs
sudo tail -f /var/log/postgresql/postgresql-14-main.log
```

### SSL Certificate Issues

```bash
# Check certificate status
sudo certbot certificates

# Force renewal
sudo certbot renew --force-renewal

# Check nginx SSL config
sudo nginx -t
```

---

## Cost Optimization

### Recommended VPS Providers

| Provider | Plan | RAM | Price | Notes |
|----------|------|-----|-------|-------|
| Hetzner | CX11 | 2GB | €4.51/mo | Best value, EU datacenters |
| DigitalOcean | Basic | 1GB | $6/mo | Good documentation |
| Linode | Nanode | 1GB | $5/mo | Reliable, good support |
| Vultr | Cloud | 1GB | $6/mo | Global locations |

### Monthly Cost Breakdown

| Item | Cost |
|------|------|
| VPS (2GB RAM) | $5-7 |
| Domain (.com) | ~$1/mo (amortized) |
| SSL Certificate | Free (Let's Encrypt) |
| Google Maps API | Free (Street View Metadata API has no cost) |
| **Total** | **~$6-8/month** |

---

## Next Steps

After deployment:

1. **Add initial data** - Trigger an update to start collecting Street View data
2. **Monitor performance** - Watch logs for the first few update cycles
3. **Set up monitoring** - Consider uptime monitoring (UptimeRobot, Healthchecks.io)
4. **Configure backups** - Set up automated database backups
5. **Document your setup** - Note any customizations you make

For daily operations, see [QUICK_REFERENCE.md](./QUICK_REFERENCE.md).
