"""
TG Drive - Standalone Python MTProto Engine
High-Performance, Direct "Saved Messages" ('me') Storage Service
Powered by FastAPI & Telethon MTProto
"""

import os
import io
import json
import asyncio
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import DocumentAttributeFilename, MessageMediaDocument

app = FastAPI(
    title="TG Drive Python MTProto API Engine",
    version="2.0.0",
    description="Direct Telegram Saved Messages Storage Engine for TG Drive"
)

# Enable CORS for all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FILE_PREFIX = "#TG_DRIVE_FILE#"

@app.get("/")
@app.get("/health")
async def health_check():
    return {
        "service": "TG Drive Python MTProto Engine",
        "version": "2.0.0",
        "storage": "Telegram 'Saved Messages' ('me')",
        "status": "healthy"
    }

async def get_tg_client(session_string: str, api_id: int, api_hash: str) -> TelegramClient:
    """Helper to initialize and connect a Telethon MTProto client"""
    client = TelegramClient(
        StringSession(session_string),
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
    """
    file_bytes = await file.read()
    file_name = file.filename or "file.bin"
    file_size = len(file_bytes)

    client = await get_tg_client(session_string, api_id, api_hash)
    try:
        # Create in-memory stream
        file_stream = io.BytesIO(file_bytes)
        file_stream.name = file_name

        caption = f'{FILE_PREFIX}{{"name":"{file_name}","folderId":"{folder_id}"}}'

        # Send directly to 'me' (Saved Messages)
        msg = await client.send_file(
            'me',
            file_stream,
            caption=caption,
            force_document=True,
            attributes=[DocumentAttributeFilename(file_name=file_name)]
        )

        return {
            "status": "success",
            "data": {
                "id": str(msg.id),
                "message_id": msg.id,
                "name": file_name,
                "size": file_size,
                "folder_id": folder_id,
                "mimeType": file.content_type or "application/octet-stream",
                "destination": "Saved Messages ('me')",
                "created_at": int(msg.date.timestamp() * 1000)
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MTProto Upload Failed: {str(e)}")
    finally:
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
    """
    client = await get_tg_client(session_string, api_id, api_hash)
    try:
        msg = await client.get_messages('me', ids=message_id)
        if not msg or not msg.media:
            raise HTTPException(status_code=404, detail="File message not found in Saved Messages")

        file_bytes = await client.download_media(msg, file=bytes)
        file_name = msg.file.name if msg.file and msg.file.name else f"file_{message_id}"

        return Response(
            content=file_bytes,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{file_name}"',
                "Access-Control-Allow-Origin": "*"
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MTProto Download Failed: {str(e)}")
    finally:
        await client.disconnect()

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
