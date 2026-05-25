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

# Upgrade dependencies automatically to ensure modern context summarization is supported
# Handles virtual environments created without pip by falling back to system pip/pip3 (which respects the active venv)
if python3 -m pip --version &>/dev/null; then
    PIP_CMD="python3 -m pip"
elif command -v pip3 &>/dev/null; then
    PIP_CMD="pip3"
elif command -v pip &>/dev/null; then
    PIP_CMD="pip"
else
    PIP_CMD=""
fi

if [ -n "$PIP_CMD" ]; then
    echo "📦 Checking and upgrading required Pipecat dependencies using $PIP_CMD..."
    $PIP_CMD install --upgrade "pipecat-ai[cartesia,daily,google,local-smart-turn-v3,openai,runner,silero,webrtc]"
else
    echo "⚠️ Warning: pip command not found. Skipping dependency check."
fi

if [ -n "$MISTRAL_API_KEY" ]; then
    echo "🔍 Fetching all available Mistral voices..."
    python3 -c '
import os, requests
api_key = os.getenv("MISTRAL_API_KEY")
try:
    voices = []
    page_size = 10
    page = 1
    seen_ids = set()
    while True:
        res = requests.get(f"https://api.mistral.ai/v1/audio/voices?page={page}&page_size={page_size}", headers={"Authorization": f"Bearer {api_key}"}, timeout=10)
        if res.status_code == 200:
            data = res.json()
            items = data.get("items", [])
            if not items:
                break
            
            new_items = [item for item in items if item.get("id") not in seen_ids]
            if not new_items:
                break
                
            for item in new_items:
                seen_ids.add(item.get("id"))
                voices.append(item)
                
            page += 1
        else:
            print(f"❌ Failed to fetch voices: {res.status_code} {res.text}")
            break
            
    print(f"\n--- Available Mistral Voices ({len(voices)} found) ---")
    for i, v in enumerate(voices):
        n = v.get("name")
        uuid = v.get("id")
        print(f"   [{i+1:03d}] Name: {n} | ID: {uuid}")
    print("-------------------------------------------\n")
except Exception as e:
    print(f"⚠️ Error fetching voices: {e}")
'
else
    echo "⚠️ Warning: MISTRAL_API_KEY environment variable not found. Skipping voice listing."
fi

echo "🚀 Starting Vedic Astrology Call Line (Webhook Mode on Port 8090)..."
echo "Log file: $LOG_FILE"
nohup python3 webhook_server.py > "$LOG_FILE" 2>&1 &

BOT_PID=$!

# Save PID
echo "$BOT_PID" > "$PID_FILE"

echo "✅ Astrology bot started with PID $BOT_PID"
echo "You can view logs with: tail -f $LOG_FILE"
