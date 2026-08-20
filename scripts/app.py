import os
import io
import json
import base64
import struct
import shutil
import asyncio
import subprocess
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

FILE_PREFIX = "#TG_DRIVE_FILE#"

DEFAULT_NGROK_TOKEN = "3IBGFjZrUBgDgqY1Hn3EIU20BXL_7DSo3P6LkVk1CrgNkg95Q"
CLOUDFLARE_BIN = "/tmp/cloudflared"
TUNNEL_LOG = "/tmp/cloudflared.log"

def normalize_telethon_session(session_str: str) -> str:
    """
    Normalizes a GramJS or Telethon string session into a standard Telethon StringSession.
    """
    if not session_str:
        return session_str

    try:
        data = base64.urlsafe_b64decode(session_str + '=' * (-len(session_str) % 4))
        
        # Format 1: Telethon format (352 or 353 bytes starting with DC byte)
        if len(data) in (352, 353, 263, 264):
            return session_str

        # Format 2: GramJS Browser Session format
        if session_str.startswith('1'):
            decoded = base64.b64decode(session_str[1:] + '=' * (-len(session_str[1:]) % 4))
            dc_id = decoded[0]
            
            # Map official Telegram DC IP addresses
            DC_IPS = {
                1: "149.154.175.53",
                2: "149.154.167.51",
                3: "149.154.175.100",
                4: "149.154.167.91",
                5: "91.108.56.130"
            }
            
            ip_str = DC_IPS.get(dc_id, "91.108.56.130")
            ip_parts = [int(p) for p in ip_str.split('.')]
            ip_bin = bytes(ip_parts)
            port = 443
            auth_key = decoded[-256:]
            
            telethon_data = struct.pack('>B4sH256s', dc_id, ip_bin, port, auth_key)
            return '1' + base64.urlsafe_b64encode(telethon_data).decode('ascii')
            
    except Exception as e:
        print(f"[Session Parse Warning] {e}")

    return session_str

async def start_auto_tunnel():
    """
    Automatically creates a high-performance permanent public tunnel using Ngrok
    with Cloudflare Tunnel as a fallback.
    """
    await asyncio.sleep(2)
    port = int(os.environ.get("PORT", "8000"))
    ngrok_token = os.environ.get("NGROK_AUTHTOKEN", DEFAULT_NGROK_TOKEN).strip()
    ngrok_domain = os.environ.get("NGROK_DOMAIN", "").strip()

    # 1. Primary: Ngrok Permanent Tunnel
    if ngrok_token:
        try:
            from pyngrok import ngrok, conf
            print("[Tunnel] Authenticating with Ngrok using permanent authtoken...")
            conf.get_default().auth_token = ngrok_token
            
            connect_kwargs = {"addr": port, "proto": "http"}
            if ngrok_domain:
                connect_kwargs["domain"] = ngrok_domain

            tunnel = ngrok.connect(**connect_kwargs)
            public_url = tunnel.public_url.replace("http://", "https://")

            print("\n" + "="*60)
            print("🌟 PUBLIC PERMANENT BACKEND URL FOR CLOUDFLARE:")
            print(f"👉 {public_url}")
            print("="*60)
            print("✅ Permanent Tunnel is LIVE! (Persistent across restarts)")
            print("="*60 + "\n")
            return
        except Exception as ngrok_err:
            print(f"[Ngrok Notice] {ngrok_err} - Falling back to Cloudflare tunnel...")

    # 2. Fallback: Standalone Cloudflare Tunnel
    try:
        if not os.path.exists(CLOUDFLARE_BIN):
            print("[Tunnel] Downloading standalone Cloudflare binary...")
            url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"
            import httpx
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as http_client:
                r = await http_client.get(url)
                if r.status_code == 200:
                    with open(CLOUDFLARE_BIN, "wb") as f:
                        f.write(r.content)
                    os.chmod(CLOUDFLARE_BIN, 0o777)
                    print("[Tunnel] Cloudflare binary installed successfully!")

        if os.path.exists(CLOUDFLARE_BIN):
            log_file = open(TUNNEL_LOG, "w")
            proc = subprocess.Popen(
                [CLOUDFLARE_BIN, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
                stdout=log_file,
                stderr=log_file
            )

            for _ in range(30):
                await asyncio.sleep(1)
                if os.path.exists(TUNNEL_LOG):
                    with open(TUNNEL_LOG, "r") as f:
                        content = f.read()
                        import re
                        match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
                        if match:
                            url = match.group(0)
                            print("\n" + "="*60)
                            print("🌟 PUBLIC BACKEND URL FOR CLOUDFLARE:")
                            print(f"👉 {url}")
                            print("="*60 + "\n")
                            break
    except Exception as err:
        print(f"[Tunnel Startup Notice] {err}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Launch auto-tunnel on startup in background
    asyncio.create_task(start_auto_tunnel())
    yield

app = FastAPI(
    title="TG Drive MTProto Microservice",
    version="3.0.0",
    description="High-performance, Zero-RAM Streaming Telegram MTProto storage backend",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "service": "TG Drive Python MTProto Engine",
        "version": "3.0.0",
        "storage": "Telegram 'Saved Messages' ('me')",
        "memory_optimization": "Zero-RAM Streaming (512KB Chunk Buffer)",
        "tunnel": "Permanent Ngrok + Cloudflare Fallback",
        "status": "healthy"
    }

async def get_tg_client(session_string: str, api_id: int, api_hash: str) -> TelegramClient:
    """Helper to initialize and connect a Telethon MTProto client"""
    formatted_session = normalize_telethon_session(session_string)
    client = TelegramClient(
        StringSession(formatted_session),
        api_id,
        api_hash,
        connection_retries=5,
        timeout=30
    )
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise HTTPException(status_code=401, detail="Invalid or expired Telegram MTProto session")
    return client

@app.post("/api/upload")
async def upload_file_to_saved_messages(
    file: UploadFile = File(...),
    session_string: str = Form(...),
    api_id: int = Form(...),
    api_hash: str = Form(...),
    folder_id: str = Form("root")
):
    """
    Uploads file DIRECTLY into user's Telegram 'Saved Messages' ('me')
    Uses streaming buffer (file.file) to keep RAM usage constant (< 10MB) even for 2GB+ files.
    """
    file_name = file.filename or "file.bin"
    file_size = file.size if hasattr(file, 'size') and file.size else 0

    client = await get_tg_client(session_string, api_id, api_hash)
    try:
        caption = f'{FILE_PREFIX}{{"name":"{file_name}","folderId":"{folder_id}"}}'

        # Set attributes and stream directly from the file handle without loading into RAM
        attributes = [DocumentAttributeFilename(file_name=file_name)]
        if file.content_type and file.content_type.startswith("video/"):
            attributes.append(DocumentAttributeVideo(duration=0, w=1280, h=720, supports_streaming=True))

        # Reset pointer to start of stream
        await file.seek(0)

        # Telethon accepts file-like objects (file.file) directly and streams in 512KB chunks
        msg = await client.send_file(
            'me',
            file.file,
            caption=caption,
            force_document=True,
            attributes=attributes
        )

        real_size = msg.file.size if msg.file and msg.file.size else file_size

        return {
            "status": "success",
            "data": {
                "id": str(msg.id),
                "message_id": msg.id,
                "name": file_name,
                "size": real_size,
                "folder_id": folder_id,
                "mimeType": file.content_type or "application/octet-stream",
                "destination": "Saved Messages ('me')",
                "created_at": int(msg.date.timestamp() * 1000)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MTProto Upload Failed: {str(e)}")
    finally:
        await file.close()
        await client.disconnect()

@app.get("/api/download/{message_id}")
async def download_file_from_saved_messages(
    message_id: int,
    session_string: str = Query(...),
    api_id: int = Query(...),
    api_hash: str = Query(...)
):
    """
    Streams file DIRECTLY from user's Telegram 'Saved Messages' ('me')
    Uses 512KB chunked streaming response so RAM usage is virtually zero.
    """
    client = await get_tg_client(session_string, api_id, api_hash)
    try:
        msg = await client.get_messages('me', ids=message_id)
        if not msg or not msg.media:
            await client.disconnect()
            raise HTTPException(status_code=404, detail="File message not found in Saved Messages")

        file_name = msg.file.name if msg.file and msg.file.name else f"file_{message_id}"
        file_size = msg.file.size if msg.file and msg.file.size else None
        mime_type = msg.file.mime_type if msg.file and msg.file.mime_type else "application/octet-stream"

        async def stream_generator():
            try:
                async for chunk in client.iter_download(msg.media, chunk_size=512*1024):
                    yield chunk
            finally:
                await client.disconnect()

        headers = {
            "Content-Disposition": f'attachment; filename="{file_name}"',
            "Access-Control-Allow-Origin": "*",
            "Accept-Ranges": "bytes"
        }
        if file_size:
            headers["Content-Length"] = str(file_size)

        return StreamingResponse(
            stream_generator(),
            media_type=mime_type,
            headers=headers
        )
    except HTTPException:
        raise
    except Exception as e:
        await client.disconnect()
        raise HTTPException(status_code=500, detail=f"MTProto Download Failed: {str(e)}")

@app.delete("/api/delete/{message_id}")
async def delete_file_from_saved_messages(
    message_id: int,
    session_string: str = Query(...),
    api_id: int = Query(...),
    api_hash: str = Query(...)
):
    """
    Deletes file DIRECTLY from user's Telegram 'Saved Messages' ('me')
    """
    client = await get_tg_client(session_string, api_id, api_hash)
    try:
        await client.delete_messages('me', [message_id], revoke=True)
        return {
            "status": "success",
            "message": f"Message #{message_id} permanently deleted from Saved Messages ('me')"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Delete Failed: {str(e)}")
    finally:
        await client.disconnect()

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
