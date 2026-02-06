# Street View Heatmap - Quick Reference

Command reference for daily operations. Keep this handy when SSH'd into your server.

---

## Service Management

```bash
# Start/stop/restart application
sudo systemctl start heatmap
sudo systemctl stop heatmap
sudo systemctl restart heatmap

# Check status
sudo systemctl status heatmap

# Enable/disable on boot
sudo systemctl enable heatmap
sudo systemctl disable heatmap
```

---

## Configuration Changes

The core workflow for any config change:

```bash
# 1. Edit the config file
nano ~/streetview-heatmap/config.yaml

# 2. Restart to apply changes
sudo systemctl restart heatmap

# 3. Verify changes applied
curl http://localhost:5001/api/config | jq
```

### Common Config Changes

**Change update frequency:**

```yaml
# In config.yaml, under scheduler:
scheduler:
  interval_hours: 24    # Daily (default)
  interval_hours: 168   # Weekly
  interval_hours: 12    # Twice daily
```

**Adjust performance:**

```yaml
# In config.yaml, under update:
update:
  batch_size: 50        # Tiles per batch (increase for faster processing)
  concurrency: 20       # Parallel requests (reduce if hitting rate limits)
```

**Disable automatic updates:**

```yaml
scheduler:
  enabled: false
```

### Performance Tuning

The Street View Metadata API supports **30k requests/min** with **unlimited daily** quota. Defaults are conservative — increase for faster coverage.

```yaml
# High-throughput config (for capable VPS with good network)
update:
  batch_size: 200         # Default: 50. Max via API: 1000
  concurrency: 100        # Default: 20. Controls parallel Street View API calls
  overpass_delay_seconds: 1  # Default: 2. Delay between Overpass queries
```

**Runtime config changes** (no restart needed):

```bash
curl -X POST http://localhost:5001/api/config \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"update": {"batch_size": 200, "concurrency": 100}}'
```

| Parameter | Default | What it controls |
|-----------|---------|------------------|
| `batch_size` | 50 | Tiles processed per job run |
| `concurrency` | 20 | Parallel Street View API requests |
| `overpass_delay_seconds` | 2 | Delay between Overpass API calls |
| `samples_per_road` | 5 | Sample points per road segment |
| `min_age_for_recheck_days` | 90 | Days before low-priority tiles are rechecked |

### Tile Priority Quick Reference

Tiles are prioritised based on city overlap (see README for full details):

- **High**: 15 major metros (London, Birmingham, Manchester, etc.) — scanned first, refreshed at 1yr and 3yr
- **Medium**: ~120 regional centres (Oxford, Cambridge, York, etc.) — refreshed at 3yr and 1yr
- **Low**: Rural/remote areas with no city overlap — refreshed after `min_age_for_recheck_days` (default 90)

Non-city tiles still have metadata collected. Tiles with zero roads return early but are retried on the next run.

---

## Viewing Logs

```bash
# Application logs (real-time follow)
sudo journalctl -u heatmap -f

# Last N lines
sudo journalctl -u heatmap -n 100
sudo journalctl -u heatmap -n 500

# Logs from specific time period
sudo journalctl -u heatmap --since today
sudo journalctl -u heatmap --since "1 hour ago"
sudo journalctl -u heatmap --since "2024-01-15" --until "2024-01-16"

# Search logs for errors
sudo journalctl -u heatmap | grep -i error

# Nginx access log
sudo tail -f /var/log/nginx/heatmap_access.log

# Nginx error log
sudo tail -f /var/log/nginx/heatmap_error.log
```

---

## Nginx Operations

```bash
# Test configuration (always do this before reload!)
sudo nginx -t

# Reload (apply config changes, no downtime)
sudo systemctl reload nginx

# Restart (if reload doesn't work)
sudo systemctl restart nginx

# View status
sudo systemctl status nginx
```

---

## Application Updates

```bash
# Full update process
cd ~/streetview-heatmap
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart heatmap

# One-liner for quick updates
cd ~/streetview-heatmap && git pull && source venv/bin/activate && pip install -r requirements.txt && sudo systemctl restart heatmap
```

---

## Database Operations

```bash
# Access PostgreSQL shell
psql -U streetview -h localhost streetview

# Quick queries
psql -U streetview -h localhost streetview -c "SELECT COUNT(*) FROM metadata;"

# Check database size
sudo -u postgres psql -c "SELECT pg_size_pretty(pg_database_size('streetview'));"

# Backup database
pg_dump -U streetview -h localhost streetview | gzip > ~/backup_$(date +%Y%m%d).sql.gz

# Restore from backup
gunzip < backup_20240115.sql.gz | psql -U streetview -h localhost streetview

# List recent backups
ls -lah ~/backup_*.sql.gz
```

---

## SSL Certificates

```bash
# List certificates
sudo certbot certificates

# Test auto-renewal
sudo certbot renew --dry-run

# Force renewal (if needed)
sudo certbot renew --force-renewal

# Check certificate expiry
echo | openssl s_client -servername yourdomain.com -connect yourdomain.com:443 2>/dev/null | openssl x509 -noout -dates
```

---

## System Monitoring

```bash
# Interactive process viewer
htop

# Memory usage
free -h

# Disk usage
df -h

# Check specific process
ps aux | grep python
ps aux | grep gunicorn

# Network connections to app
sudo netstat -tulpn | grep 5001

# System load
uptime

# Recent system messages
dmesg | tail -20
```

---

## API Operations

### Health Checks

```bash
# Basic health check
curl http://localhost:5001/api/health | jq

# Full status (includes coverage stats)
curl http://localhost:5001/api/status | jq

# Check from external (via nginx)
curl https://yourdomain.com/api/health | jq
```

### Trigger Manual Update

```bash
# Basic trigger (processes all priorities using smart refresh strategy)
curl -X POST http://localhost:5001/api/update/trigger \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json"

# Scan only high-priority tiles (major cities: London, Birmingham, etc.)
curl -X POST http://localhost:5001/api/update/trigger \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"priority": "high", "tile_limit": 100}'

# Scan medium-priority tiles (regional centres)
curl -X POST http://localhost:5001/api/update/trigger \
  -H "X-API-Key: YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"priority": "medium", "tile_limit": 50}'

# Check update status
curl http://localhost:5001/api/update/status | jq

# Watch progress in real-time (repeat until complete)
watch -n 5 'curl -s http://localhost:5001/api/update/status | jq ".percent_complete, .tiles_processed, .tiles_total"'
```

**Note:** Only one job can run at a time. `tile_limit` accepts 1-1000.

### View Configuration

```bash
# Current runtime config
curl http://localhost:5001/api/config | jq

# Pretty print specific sections
curl http://localhost:5001/api/config | jq '.scheduler'
curl http://localhost:5001/api/config | jq '.update'
```

### Data Queries

```bash
# List tiles
curl "http://localhost:5001/api/tiles?limit=10" | jq

# Get tile data
curl "http://localhost:5001/api/tiles/TILE_ID/data" | jq

# List cities
curl http://localhost:5001/api/cities | jq
```

---

## Troubleshooting Quick Fixes

### App Not Responding

```bash
# Check if running
sudo systemctl status heatmap

# Restart it
sudo systemctl restart heatmap

# Still broken? Check logs
sudo journalctl -u heatmap -n 50
```

### 502 Bad Gateway

```bash
# Flask not running - restart it
sudo systemctl restart heatmap

# Check it's listening
curl http://localhost:5001/api/health

# Check nginx config
sudo nginx -t && sudo systemctl reload nginx
```

### High Memory Usage

```bash
# Check what's using memory
htop

# Reduce concurrency in config
nano ~/streetview-heatmap/config.yaml
# Set: update.concurrency: 10

sudo systemctl restart heatmap
```

### Disk Space Issues

```bash
# Check usage
df -h

# Clear old logs
sudo journalctl --vacuum-time=7d

# Find large files
sudo find / -type f -size +100M 2>/dev/null

# Clean old backups (keep last 7)
ls -t ~/backup_*.sql.gz | tail -n +8 | xargs rm -f
```

### Database Connection Failed

```bash
# Check PostgreSQL
sudo systemctl status postgresql

# Restart if needed
sudo systemctl restart postgresql

# Test connection
psql -U streetview -h localhost streetview -c "SELECT 1;"
```

---

## Environment Variables

View current `.env`:

```bash
cat ~/streetview-heatmap/.env
```

Edit `.env`:

```bash
nano ~/streetview-heatmap/.env

# After editing, restart to apply
sudo systemctl restart heatmap
```

Generate new API key:

```bash
openssl rand -hex 32
```

---

## Useful File Locations

| What | Where |
|------|-------|
| Application code | `~/streetview-heatmap/` |
| Configuration | `~/streetview-heatmap/config.yaml` |
| Environment vars | `~/streetview-heatmap/.env` |
| Virtual environment | `~/streetview-heatmap/venv/` |
| Application logs | `journalctl -u heatmap` |
| Nginx config | `/etc/nginx/sites-available/heatmap` |
| Nginx logs | `/var/log/nginx/heatmap_*.log` |
| SSL certificates | `/etc/letsencrypt/live/yourdomain.com/` |
| Systemd service | `/etc/systemd/system/heatmap.service` |
| Database backups | `~/backup_*.sql.gz` |

---

## Keyboard Shortcuts (nano editor)

| Action | Keys |
|--------|------|
| Save | `Ctrl+O`, `Enter` |
| Exit | `Ctrl+X` |
| Save and Exit | `Ctrl+X`, `Y`, `Enter` |
| Search | `Ctrl+W` |
| Find & Replace | `Ctrl+\` |
| Go to line | `Ctrl+_` |
| Cut line | `Ctrl+K` |
| Paste | `Ctrl+U` |

---

## Emergency Procedures

### Complete Service Restart

```bash
sudo systemctl restart postgresql
sudo systemctl restart heatmap
sudo systemctl restart nginx
```

### Roll Back Code Update

```bash
cd ~/streetview-heatmap
git log --oneline -5  # Find previous commit
git checkout COMMIT_HASH
sudo systemctl restart heatmap
```

### Restore Database from Backup

```bash
# Stop application
sudo systemctl stop heatmap

# Restore
gunzip < backup_YYYYMMDD.sql.gz | psql -U streetview -h localhost streetview

# Restart
sudo systemctl start heatmap
```
