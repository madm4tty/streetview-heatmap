#!/usr/bin/env bash
# =============================================================================
# deploy.sh — Pull latest code and restart the heatmap service
#
# Usage:
#   Directly on the server:
#     bash scripts/deploy.sh
#
#   Remotely from another machine:
#     ssh your-server 'bash ~/streetview-heatmap/scripts/deploy.sh'
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration — adjust PROJECT_DIR if the repo lives somewhere else
# ---------------------------------------------------------------------------
PROJECT_DIR="${PROJECT_DIR:-$HOME/streetview-heatmap}"
VENV_DIR="$PROJECT_DIR/venv"
SERVICE_NAME="heatmap"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ts() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

die() {
    ts "ERROR: $*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Step 1: Navigate to project directory
# ---------------------------------------------------------------------------
ts "Deploying from $PROJECT_DIR"
cd "$PROJECT_DIR" || die "Project directory not found: $PROJECT_DIR"

# ---------------------------------------------------------------------------
# Step 2: Pull latest code
# ---------------------------------------------------------------------------
ts "Pulling latest code from git..."
git pull || die "git pull failed — check your remote and network connection"
ts "Code up to date."

# ---------------------------------------------------------------------------
# Step 3: Activate virtual environment
# ---------------------------------------------------------------------------
ts "Activating virtual environment..."
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate" || die "Virtual environment not found at $VENV_DIR — run: python3 -m venv venv"

# ---------------------------------------------------------------------------
# Step 4: Install / update dependencies
# ---------------------------------------------------------------------------
ts "Installing dependencies..."
pip install -q -r requirements.txt
ts "Dependencies installed."

# ---------------------------------------------------------------------------
# Step 5: Run pending database migrations
# ---------------------------------------------------------------------------
ts "Running database migrations..."
shopt -s nullglob
migration_count=0
for migration in migrations/*.py; do
    ts "  Running $migration"
    python3 "$migration" || die "Migration failed: $migration"
    migration_count=$((migration_count + 1))
done
if [ "$migration_count" -eq 0 ]; then
    ts "  No migration files found — skipping."
else
    ts "  $migration_count migration(s) completed."
fi

# ---------------------------------------------------------------------------
# Step 6: Restart the systemd service
# ---------------------------------------------------------------------------
ts "Restarting $SERVICE_NAME service..."
sudo systemctl restart "$SERVICE_NAME" || die "Failed to restart $SERVICE_NAME service"
ts "Service restart command issued."

# ---------------------------------------------------------------------------
# Step 7: Wait and verify the service is running
# ---------------------------------------------------------------------------
ts "Waiting 3 seconds for service to stabilise..."
sleep 3

ts "Checking service status..."
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    ts "Service '$SERVICE_NAME' is active and running."
else
    ts "Service '$SERVICE_NAME' did not come up. Recent logs:"
    sudo journalctl -u "$SERVICE_NAME" -n 20 --no-pager >&2
    die "Service '$SERVICE_NAME' is not active after restart"
fi

ts "Deploy complete."
