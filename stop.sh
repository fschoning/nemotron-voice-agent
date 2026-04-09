#!/bin/bash
# Stop script for the Vedic Astrology Call Line bot
#
# Usage: ./scripts/stop_astrology.sh

set -e

# Get the project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

PID_FILE="$PROJECT_ROOT/logs/astrology_bot.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "⚠️ No PID file found at $PID_FILE. Astrology bot might not be running."
    
    # Fallback to pgrep
    PID=$(pgrep -f "apps/astrology_call_line.py") || true
    if [ -n "$PID" ]; then
        echo "Found process via pgrep: $PID"
    else
        echo "❌ Astrology bot process not found."
        exit 0
    fi
else
    PID=$(cat "$PID_FILE")
fi

echo "🛑 Stopping Astrology bot (PID: $PID)..."

# Try graceful shutdown (SIGTERM)
kill -TERM "$PID" 2>/dev/null || true

# Wait a bit for it to finish
for i in {1..5}; do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "✅ Bot stopped."
        [ -f "$PID_FILE" ] && rm "$PID_FILE"
        exit 0
    fi
    sleep 1
done

# Force kill if still running
echo "⚠️ Bot did not stop gracefully, force killing..."
kill -9 "$PID" 2>/dev/null || true
[ -f "$PID_FILE" ] && rm "$PID_FILE"

echo "✅ Bot killed."
