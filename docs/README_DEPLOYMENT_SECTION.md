# Production Deployment

This section describes how to deploy the Street View Heatmap to a production VPS.

## Architecture

The production deployment uses a **simplified single-frontend architecture**:

```
Internet → Nginx (SSL/proxy) → Flask/Gunicorn → PostgreSQL/PostGIS
                ↓
         yourdomain.com
```

**Key characteristics:**

- **Public pages**: Map (`/`), Dashboard (`/dashboard`), Instructions (`/instructions`)
- **API endpoints**: All `/api/*` routes proxied through, POST requires `X-API-Key`
- **Blocked routes**: `/config` returns 404 (no web admin interface)
- **Configuration**: Managed via SSH + editing `config.yaml` + service restart

This approach reduces attack surface and simplifies maintenance. If you're SSH'd into the server anyway, editing a config file is straightforward.

## Quick Start

1. Get a VPS (2GB RAM minimum, Ubuntu 22.04)
2. Follow [docs/DEPLOYMENT.md](./docs/DEPLOYMENT.md) step-by-step
3. Point your domain's A record to the server IP
4. Run `sudo certbot --nginx -d yourdomain.com` for SSL

## Configuration Management

All configuration changes happen via SSH:

```bash
# SSH into server
ssh heatmap@yourdomain.com

# Edit configuration
nano ~/streetview-heatmap/config.yaml

# Restart to apply
sudo systemctl restart heatmap

# Verify
curl http://localhost:5001/api/config | jq
```

Common settings in `config.yaml`:

```yaml
scheduler:
  enabled: true
  interval_hours: 24     # Update frequency

update:
  batch_size: 50         # Tiles per batch
  concurrency: 20        # Parallel requests
```

## Security Features

- **SSL/TLS**: Free certificates via Let's Encrypt
- **API authentication**: POST endpoints require `X-API-Key` header
- **Security headers**: X-Frame-Options, X-Content-Type-Options, etc.
- **Rate limiting**: 100 requests/minute per IP on API endpoints
- **Firewall**: UFW configured to allow only SSH, HTTP, HTTPS
- **No web admin**: Reduced attack surface, no web-based authentication to compromise

## Estimated Costs

| Item | Monthly Cost |
|------|-------------|
| VPS (2GB RAM) | $5-7 |
| Domain | ~$1 (amortized) |
| SSL | Free |
| Google Maps API | Free |
| **Total** | **~$6-8** |

## Documentation

- **[DEPLOYMENT.md](./docs/DEPLOYMENT.md)** - Complete setup guide
- **[QUICK_REFERENCE.md](./docs/QUICK_REFERENCE.md)** - Command cheat sheet

## Local Development

Local development is unchanged. Run the Flask development server directly:

```bash
# Activate virtual environment
source venv/bin/activate

# Start development server
python run.py
```

This gives you full access to all routes including `/config` for testing.

## File Structure

```
nginx/
└── heatmap.conf              # Nginx reverse proxy configuration

docs/
├── DEPLOYMENT.md             # Full deployment walkthrough
├── QUICK_REFERENCE.md        # Quick command reference
└── README_DEPLOYMENT_SECTION.md  # This section
```
