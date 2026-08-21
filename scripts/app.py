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

CLOUDFLARE_BIN = "/tmp/cloudflared"
TUNNEL_LOG = "/tmp/cloudflared.log"
FIREBASE_DB_URL = "https://studio-2641678334-61796-default-rtdb.firebaseio.com"
SYNC_SECRET = os.environ.get("SYNC_SECRET", "tgdrive_live_auto_sync_secret_2026")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "https://tgdriveapi.youganksaini1.workers.dev")

async def sync_url_to_gateway(url: str):
    """Auto-syncs live tunnel URL directly to Global Database & Cloudflare Gateway"""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=15.0) as client:
            # 1. Direct Firebase RTDB sync (Global Instant Multi-Region Sync)
            try:
                await client.put(
                    f"{FIREBASE_DB_URL}/system/live_backend.json",
                    json={"backend_url": url, "updated_at": asyncio.get_event_loop().time()}
                )
                await client.put(
                    f"{FIREBASE_DB_URL}/users/system/live_backend.json",
                    json={"backend_url": url, "updated_at": asyncio.get_event_loop().time()}
                )
                print("✅ [Auto-Sync] Live backend successfully synchronized to Global Database!")
            except Exception as fb_err:
                print(f"[Firebase Sync Notice] {fb_err}")

            # 2. Cloudflare Gateway ping
            try:
                res = await client.post(
                    f"{GATEWAY_URL}/internal/update-tunnel",
                    headers={"X-Sync-Secret": SYNC_SECRET},
                    json={"backend_url": url}
                )
                if res.status_code == 200:
                    print("✅ [Auto-Sync] Live backend successfully synchronized to Cloudflare Gateway!")
            except Exception:
                pass
    except Exception as e:
        print(f"[Auto-Sync Error] {e}")

async def start_auto_tunnel():
    """
    Continuous Self-Healing Supervisor:
    Launches and monitors Cloudflare Unlimited Tunnel.
    If the tunnel drops or process restarts, it immediately relaunches
    and synchronizes the new URL to the Cloudflare Gateway.
    """
    await asyncio.sleep(2)
    port = int(os.environ.get("PORT", "8000"))

    # Download Cloudflare binary if not present
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
    except Exception as e:
        print(f"[Tunnel Binary Error] {e}")

    while True:
        try:
            if os.path.exists(CLOUDFLARE_BIN):
                try:
                    subprocess.run(["pkill", "-9", "-f", "cloudflared"], capture_output=True)
                except Exception:
                    pass
                await asyncio.sleep(1)

                if os.path.exists(TUNNEL_LOG):
                    try:
                        os.remove(TUNNEL_LOG)
                    except Exception:
                        pass

                log_file = open(TUNNEL_LOG, "w")
                proc = subprocess.Popen(
                    [CLOUDFLARE_BIN, "tunnel", "--url", f"http://127.0.0.1:{port}", "--protocol", "http2", "--no-autoupdate"],
                    stdout=log_file,
                    stderr=log_file,
                    start_new_session=True
                )

                discovered_url = None
                for _ in range(35):
                    await asyncio.sleep(1)
                    if os.path.exists(TUNNEL_LOG):
                        with open(TUNNEL_LOG, "r") as f:
                            content = f.read()
                            import re
                            match = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", content)
                            if match:
                                discovered_url = match.group(0)
                                print("\n" + "="*60)
                                print("🌟 LIVE UNLIMITED CLOUDFLARE TUNNEL URL:")
                                print(f"👉 {discovered_url}")
                                print("="*60)
                                print("🚀 Bandwidth: UNLIMITED (Zero Data Cap / 100% Free)")
                                print("="*60 + "\n")
                                break

                if discovered_url:
                    # Allow 4 seconds for Cloudflare Global DNS propagation
                    await asyncio.sleep(4)
                    await sync_url_to_gateway(discovered_url)

                    # Active watchdog: checks if tunnel is alive and detects url renewals
                    ping_counter = 0
                    consecutive_failures = 0
                    while proc.poll() is None:
                        await asyncio.sleep(10)
                        ping_counter += 1

                        # 1. Check if cloudflared reconnected with a newer URL
                        if os.path.exists(TUNNEL_LOG):
                            try:
                                with open(TUNNEL_LOG, "r") as f:
                                    import re
                                    all_matches = re.findall(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", f.read())
                                    if all_matches and all_matches[-1] != discovered_url:
                                        discovered_url = all_matches[-1]
                                        print(f"🔄 [Tunnel] Detected updated tunnel URL: {discovered_url}")
                                        consecutive_failures = 0
                                        await sync_url_to_gateway(discovered_url)
                            except Exception:
                                pass

                        # 2. Periodic sync ping every 2 minutes
                        if ping_counter >= 12:
                            ping_counter = 0
                            await sync_url_to_gateway(discovered_url)

                        # 3. Active probe: ensure tunnel is reachable (require 4 consecutive failures before restarting)
                        try:
                            import httpx
                            async with httpx.AsyncClient(timeout=8.0) as probe_client:
                                probe_res = await probe_client.get(f"{discovered_url}/health")
                                if probe_res.status_code == 200:
                                    consecutive_failures = 0
                                else:
                                    consecutive_failures += 1
                                    if consecutive_failures >= 4:
                                        print(f"⚠️ [Watchdog] Tunnel unresponsive ({consecutive_failures} failed probes). Restarting tunnel...")
                                        try:
                                            import signal
                                            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                                        except Exception:
                                            try: proc.kill()
                                            except Exception: pass
                                        break
                        except Exception:
                            consecutive_failures += 1
                            if consecutive_failures >= 4:
                                print(f"⚠️ [Watchdog] Tunnel probe unreachable ({consecutive_failures} failed probes). Reconnecting...")
                                try:
                                    import signal
                                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                                except Exception:
                                    try:
                                        proc.kill()
                                    except Exception:
                                        pass
                                break

                print("⚠️ [Tunnel Supervisor] Tunnel process exited. Re-launching in 2 seconds...")
                await asyncio.sleep(2)

        except Exception as err:
            print(f"[Tunnel Supervisor Notice] {err}")
            await asyncio.sleep(4)

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

        try:
            file.file.name = file_name
        except Exception:
            pass

        # Telethon streams chunks directly without loading file into RAM
        uploaded_file = await client.upload_file(
            file.file,
            file_name=file_name
        )

        msg = await client.send_file(
            'me',
            uploaded_file,
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

@app.get("/api/files")
async def list_telegram_saved_messages_files(
    session_string: str = Query(...),
    api_id: int = Query(...),
    api_hash: str = Query(...),
    limit: int = Query(100),
    offset_id: int = Query(0),
    search: Optional[str] = Query(None)
):
    """
    Directly scans and streams ALL files directly from Telegram 'Saved Messages' ('me')
    via MTProto with pagination, supporting 1000+ files!
    """
    client = await get_tg_client(session_string, api_id, api_hash)
    try:
        items = []
        async for msg in client.iter_messages('me', limit=limit, offset_id=offset_id):
            if msg.media:
                file_name = None
                file_size = 0
                mime_type = "application/octet-stream"

                if hasattr(msg, 'file') and msg.file:
                    file_name = msg.file.name
                    file_size = msg.file.size or 0
                    mime_type = msg.file.mime_type or "application/octet-stream"

                if not file_name:
                    if hasattr(msg.media, 'document'):
                        for attr in getattr(msg.media.document, 'attributes', []):
                            if hasattr(attr, 'file_name'):
                                file_name = attr.file_name
                    elif hasattr(msg.media, 'photo'):
                        file_name = f"photo_{msg.id}.jpg"
                        mime_type = "image/jpeg"

                if not file_name:
                    file_name = f"media_{msg.id}"

                # Parse folderId from caption if exists
                folder_id = "root"
                if msg.message and msg.message.startswith(FILE_PREFIX):
                    try:
                        meta = json.loads(msg.message[len(FILE_PREFIX):])
                        folder_id = meta.get("folderId", "root")
                        file_name = meta.get("name", file_name)
                    except Exception:
                        pass

                if search and search.lower() not in file_name.lower():
                    continue

                items.append({
                    "id": str(msg.id),
                    "message_id": msg.id,
                    "name": file_name,
                    "size": file_size,
                    "folder_id": folder_id,
                    "mimeType": mime_type,
                    "destination": "Saved Messages ('me')",
                    "created_at": int(msg.date.timestamp() * 1000) if msg.date else 0
                })

        return {
            "status": "success",
            "total": len(items),
            "storage": "Telegram 'Saved Messages' ('me')",
            "items": items
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to scan Saved Messages: {str(e)}")
    finally:
        await client.disconnect()

@app.get("/api/thumbnail/{message_id}")
async def get_media_thumbnail(
    message_id: int,
    session_string: str = Query(...),
    api_id: int = Query(...),
    api_hash: str = Query(...)
):
    """
    Downloads and streams the 10KB fast thumbnail preview for photos/videos from Saved Messages
    """
    client = await get_tg_client(session_string, api_id, api_hash)
    try:
        msg = await client.get_messages('me', ids=message_id)
        if not msg or not msg.media:
            await client.disconnect()
            raise HTTPException(status_code=404, detail="Media not found")

        thumb_bytes = await client.download_media(msg, thumb=-1, file=bytes)
        await client.disconnect()

        if not thumb_bytes:
            raise HTTPException(status_code=404, detail="Thumbnail not available for this media")

        return Response(
            content=thumb_bytes,
            media_type="image/jpeg",
            headers={"Cache-Control": "public, max-age=604800"}
        )
    except HTTPException:
        raise
    except Exception as e:
        await client.disconnect()
        raise HTTPException(status_code=500, detail=f"Thumbnail extraction failed: {str(e)}")

@app.post("/api/copy/{message_id}")
async def copy_saved_message_file(
    message_id: int,
    session_string: str = Query(...),
    api_id: int = Query(...),
    api_hash: str = Query(...)
):
    """
    Instantly copies/forwards message in Saved Messages ('me') without re-uploading bytes
    """
    client = await get_tg_client(session_string, api_id, api_hash)
    try:
        new_msg = await client.forward_messages('me', [message_id])
        if isinstance(new_msg, list):
            new_msg = new_msg[0]
        await client.disconnect()
        return {
            "status": "success",
            "message": "File cloned successfully in Saved Messages",
            "new_message_id": new_msg.id
        }
    except Exception as e:
        await client.disconnect()
        raise HTTPException(status_code=500, detail=f"File copy failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
