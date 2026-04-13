#!/bin/bash
# Start script for the Vedic Astrology Call Line bot
#
# Usage: ./scripts/start_astrology.sh

set -e

# Get the project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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

ZOOM_ARG=""
BOT_NAME_ARG="Vedic Pathway Astrologer"

while [[ "$#" -gt 0 ]]; do
    case $1 in
        --zoom) ZOOM_ARG="$2"; shift ;;
        --bot-name) BOT_NAME_ARG="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

if [ -z "$ZOOM_ARG" ]; then
    echo "🚀 Initializing voice selection..."
    export MISTRAL_VOICE_ID=$(python3 apps/astrology_call_line.py --voice-selector)
    echo "🚀 Starting Vedic Astrology Call Line..."
    echo "Log file: $LOG_FILE"
    nohup python3 apps/astrology_call_line.py > "$LOG_FILE" 2>&1 &
else
    echo "🚀 Starting Zoom standby mode..."
    echo "Log file: $LOG_FILE"
    nohup python3 apps/astrology_call_line.py --zoom "$ZOOM_ARG" --bot-name "$BOT_NAME_ARG" > "$LOG_FILE" 2>&1 &
fi

BOT_PID=$!

# Save PID
echo "$BOT_PID" > "$PID_FILE"

echo "✅ Astrology bot started with PID $BOT_PID"
echo "You can view logs with: tail -f $LOG_FILE"
