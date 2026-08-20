#!/bin/bash
# TG Drive Python MTProto Engine Startup Script for VPS & Runners

echo "=================================================="
echo "🚀 Setting up TG Drive Python MTProto Engine..."
echo "=================================================="

# Detect Public IP
PUBLIC_IP=$(curl -s --max-time 3 https://ifconfig.me 2>/dev/null || curl -s --max-time 3 https://api.ipify.org 2>/dev/null || echo "0.0.0.0")

# Setup Virtual Environment if not exists
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

# Install dependencies
pip install --no-cache-dir -r requirements.txt

echo "=================================================="
echo "✅ TG Drive MTProto Engine is LIVE!"
echo "🌐 Direct Public URL: http://${PUBLIC_IP}:8000"
echo "=================================================="

# If npx or cloudflared is available, auto-create a free HTTPS Public URL
if command -v cloudflared &> /dev/null; then
    echo "🔗 Starting Cloudflare HTTPS Tunnel in background..."
    cloudflared tunnel --url http://localhost:8000 &
elif command -v npx &> /dev/null; then
    echo "🔗 Starting Automatic HTTPS Tunnel via npx..."
    npx -y cloudflared tunnel --url http://localhost:8000 &
fi

# Start Uvicorn Server
uvicorn app:app --host 0.0.0.0 --port 8000
