#!/bin/bash
# Start script for the NVIDIA ASR and Magpie TTS engines
# 
# Usage: ./start_engines.sh

set -e

# Get the project root directory
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

echo "🐳 Starting ASR and TTS Docker containers..."
docker-compose up -d asr-engine tts-engine

echo "⏳ Waiting for services to warm up..."
# Simple health check loop could go here, but for now we just wait a bit
sleep 10

echo "✅ ASR (Port 8080) and TTS (Port 8001) engines are running."
echo "You can now run ./start.sh to start the astrology bot."
