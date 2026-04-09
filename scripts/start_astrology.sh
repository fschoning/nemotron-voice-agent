#!/bin/bash
# Start script for the Vedic Astrology Call Line bot
#
# Usage: ./scripts/start_astrology.sh

set -e

# Get the project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# Log directory
LOG_DIR="$PROJECT_ROOT/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/astrology_bot.log"
PID_FILE="$LOG_DIR/astrology_bot.pid"

# Check if bot is already running
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "❌ Astrology bot is already running (PID: $PID)"
        exit 1
    else
        rm "$PID_FILE"
    fi
fi

# Activate virtual environment
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "⚠️ Warning: .venv not found. Running with system python."
fi

echo "🚀 Starting Vedic Astrology Call Line..."
echo "Log file: $LOG_FILE"

# Start the bot in the background
# Use nohup to keep it running and redirect output to log
nohup python3 apps/astrology_call_line.py > "$LOG_FILE" 2>&1 &
BOT_PID=$!

# Save PID
echo "$BOT_PID" > "$PID_FILE"

echo "✅ Astrology bot started with PID $BOT_PID"
echo "You can view logs with: tail -f $LOG_FILE"
