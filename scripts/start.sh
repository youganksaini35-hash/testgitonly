#!/bin/bash
# TG Drive Python MTProto Engine Startup Script for VPS

echo "Setting up Python virtual environment..."
python3 -m venv venv
source venv/bin/activate

echo "Installing high-performance dependencies (FastAPI, Telethon, TgCrypto)..."
pip install -r requirements.txt

echo "Starting TG Drive MTProto Engine on port 8000..."
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4
