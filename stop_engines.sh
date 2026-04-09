#!/bin/bash
# Stop script for the NVIDIA ASR and Magpie TTS engines
# 
# Usage: ./stop_engines.sh

set -e

# Get the project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "🛑 Stopping ASR and TTS Docker containers..."
docker-compose stop asr-engine tts-engine

echo "✅ Engines stopped."
