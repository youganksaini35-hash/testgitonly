# 🚀 TG Drive - Python MTProto + Cloudflare Worker API Gateway

High-performance, standalone REST API Gateway for uploading, streaming, and managing files **100% DIRECTLY in Telegram "Saved Messages" (`'me'`)** using User MTProto Sessions.

---

## 🏗️ Architecture

```
[User / Client / cURL] 
         │
         ▼  (Bearer Auth & Routing)
[Cloudflare Worker Gateway: https://tgdriveapi.youganksaini1.workers.dev]
         │
         ▼  (Fast Proxy)
[Python MTProto Engine: http://YOUR_VPS_IP:8000]
         │
         ▼  (Direct C-Speed MTProto via Telethon + TgCrypto)
[Telegram User "Saved Messages" ('me')]
```

---

## 🛠️ How to Deploy on Your VPS (Choose Any 1 Method)

### Method 1: Docker (1-Command Run) ⭐ Recommended

```bash
# Clone the repository
git clone https://github.com/v54087912-collab/tgdriveapi.git
cd tgdriveapi

# Start with Docker Compose
docker compose up -d
```

---

### Method 2: Direct Python with Startup Script

```bash
# 1. Clone repository
git clone https://github.com/v54087912-collab/tgdriveapi.git
cd tgdriveapi

# 2. Run startup script (creates venv and installs dependencies)
chmod +x start.sh
./start.sh
```

---

### Method 3: 24/7 Systemd Background Service

```bash
# Create systemd service
sudo nano /etc/systemd/system/tgdriveapi.service
```

Paste this configuration:
```ini
[Unit]
Description=TG Drive Python MTProto Engine
After=network.target

[Service]
User=root
WorkingDirectory=/root/tgdriveapi
ExecStart=/root/tgdriveapi/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable & Start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable tgdriveapi
sudo systemctl start tgdriveapi
```

---

## 🔗 Connect Cloudflare Worker to Your VPS

In your Cloudflare Dashboard:
1. Go to **Workers & Pages** ➔ **`tgdriveapi`** ➔ **Settings** ➔ **Variables and Secrets**.
2. Add a Variable:
   * **Variable Name:** `PYTHON_BACKEND_URL`
   * **Value:** `http://YOUR_VPS_IP:8000` (or `https://your-domain.com`)
3. Click **Save and Deploy**!

---

## 📤 API Endpoints

### 1. Upload File (Directly into Saved Messages)
```bash
curl -X POST "https://tgdriveapi.youganksaini1.workers.dev/v1/files/upload" \
     -H "Authorization: Bearer YOUR_TGDRIVE_API_KEY" \
     -F "file=@/path/to/Video.mp4" \
     -F "folder_id=root"
```

### 2. Download File
```bash
curl -O -H "Authorization: Bearer YOUR_TGDRIVE_API_KEY" \
     "https://tgdriveapi.youganksaini1.workers.dev/v1/files/MESSAGE_ID/download"
```

### 3. List Files
```bash
curl -H "Authorization: Bearer YOUR_TGDRIVE_API_KEY" \
     "https://tgdriveapi.youganksaini1.workers.dev/v1/files"
```

### 4. Delete File
```bash
curl -X DELETE -H "Authorization: Bearer YOUR_TGDRIVE_API_KEY" \
     "https://tgdriveapi.youganksaini1.workers.dev/v1/files/MESSAGE_ID"
```
