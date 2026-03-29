#!/usr/bin/env bash
# =============================================================================
# reset_stale_tiles.sh — Shift last_checked back by 90 days for high & medium
#                        priority tiles, forcing the scheduler to re-process them.
#
# Usage:
#   Directly on the server:
#     bash scripts/reset_stale_tiles.sh
#
#   Remotely:
#     ssh your-server 'bash ~/streetview-heatmap/scripts/reset_stale_tiles.sh'
#
#   Dry run (show counts without changing anything):
#     bash scripts/reset_stale_tiles.sh --dry-run
# =============================================================================

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/streetview-heatmap}"
SHIFT_DAYS="${SHIFT_DAYS:-90}"
DRY_RUN=false

for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=true ;;
        *) echo "Unknown option: $arg"; exit 1 ;;
    esac
done

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ts() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

run_sql() {
    docker compose -f "$PROJECT_DIR/docker-compose.prod.yml" exec -T postgres \
        psql -U streetview -d streetview -t -A -c "$1"
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
ts "Connecting to database..."
if ! run_sql "SELECT 1;" >/dev/null 2>&1; then
    ts "ERROR: Cannot connect to PostgreSQL. Is docker compose running?"
    exit 1
fi
ts "Connected."

# ---------------------------------------------------------------------------
# Show current state
# ---------------------------------------------------------------------------
ts "=== Current tile counts ==="

HIGH_TOTAL=$(run_sql "SELECT COUNT(DISTINCT tile_id) FROM metadata WHERE priority = 'high';")
MEDIUM_TOTAL=$(run_sql "SELECT COUNT(DISTINCT tile_id) FROM metadata WHERE priority = 'medium';")
ts "  High priority tiles:   $HIGH_TOTAL"
ts "  Medium priority tiles: $MEDIUM_TOTAL"

HIGH_ROWS=$(run_sql "SELECT COUNT(*) FROM metadata WHERE priority = 'high';")
MEDIUM_ROWS=$(run_sql "SELECT COUNT(*) FROM metadata WHERE priority = 'medium';")
ts "  High priority rows:    $HIGH_ROWS"
ts "  Medium priority rows:  $MEDIUM_ROWS"
TOTAL_ROWS=$((HIGH_ROWS + MEDIUM_ROWS))
ts "  Total rows to update:  $TOTAL_ROWS"

ts ""
ts "=== Freshness before reset ==="
for prio in high medium; do
    FRESH=$(run_sql "
        SELECT COUNT(*) FROM metadata
        WHERE priority = '$prio'
          AND last_checked > NOW() - INTERVAL '${SHIFT_DAYS} days';
    ")
    STALE=$(run_sql "
        SELECT COUNT(*) FROM metadata
        WHERE priority = '$prio'
          AND (last_checked <= NOW() - INTERVAL '${SHIFT_DAYS} days' OR last_checked IS NULL);
    ")
    ts "  $prio: $FRESH fresh, $STALE already stale"
done

if [ "$DRY_RUN" = true ]; then
    ts ""
    ts "DRY RUN — no changes made."
    exit 0
fi

# ---------------------------------------------------------------------------
# Perform the reset
# ---------------------------------------------------------------------------
ts ""
ts "Shifting last_checked back by ${SHIFT_DAYS} days for high & medium tiles..."

for prio in high medium; do
    ROW_COUNT=$(run_sql "
        UPDATE metadata
        SET last_checked = last_checked - INTERVAL '${SHIFT_DAYS} days'
        WHERE priority = '$prio';
        SELECT COUNT(*) FROM metadata WHERE priority = '$prio';
    " | tail -1)
    ts "  $prio: updated $ROW_COUNT rows"
done

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
ts ""
ts "=== Freshness after reset ==="
for prio in high medium; do
    STALE=$(run_sql "
        SELECT COUNT(*) FROM metadata
        WHERE priority = '$prio'
          AND (last_checked <= NOW() - INTERVAL '30 days' OR last_checked IS NULL);
    ")
    TOTAL=$(run_sql "SELECT COUNT(*) FROM metadata WHERE priority = '$prio';")
    ts "  $prio: $STALE / $TOTAL rows now stale"
done

ts ""
ts "Done. The scheduler will pick up these tiles on its next run."
