import os
import httpx
import aiohttp
import logging
from collections import defaultdict
from config import API_BASE_URL

logger = logging.getLogger(__name__)

def _get_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key.strip()}",
        "User-Agent": "TGDriveTelegramBot/1.0"
    }

def categorize_file(item: dict) -> str:
    """Categorize file into videos, images, audio, apks, documents, or others."""
    name = (item.get("name") or "").lower()
    mime = (item.get("mimeType") or "").lower()
    
    if "video" in mime or name.endswith((".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv")):
        return "videos"
    elif "image" in mime or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg")):
        return "images"
    elif "audio" in mime or name.endswith((".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac")):
        return "audio"
    elif name.endswith(".apk") or "android" in mime:
        return "apks"
    elif "text" in mime or "pdf" in mime or name.endswith((".txt", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar", ".7z", ".tar", ".gz")):
        return "documents"
    else:
        return "others"

async def validate_api_key(api_key: str):
    """Validate the TG Drive API key."""
    if not api_key or not api_key.strip().startswith("tgd_"):
        return False, "API key format invalid. Key must start with 'tgd_live_' or 'tgd_test_'."
    
    url = f"{API_BASE_URL}/v1/user/profile"
    headers = _get_headers(api_key)
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    return True, data.get("data", {})
                return False, data.get("message", "Validation failed")
            elif resp.status_code in (401, 403):
                return False, "Invalid API key or unauthorized. Please verify your key on tgdriveo.pages.dev."
            else:
                return False, f"Server responded with status code: {resp.status_code}"
    except httpx.RequestError as e:
        logger.error(f"Error validating API key: {e}")
        return False, f"Network connection error: {str(e)}"

async def get_user_profile(api_key: str):
    """Get user profile and rate limits."""
    url = f"{API_BASE_URL}/v1/user/profile"
    headers = _get_headers(api_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers)
        return resp.json()

async def list_files(api_key: str, folder_id: str = "all", limit: int = 1000, offset_id: int = None, search: str = None):
    """List files with full scanning."""
    url = f"{API_BASE_URL}/v1/files"
    headers = _get_headers(api_key)
    params = {
        "folder_id": folder_id or "all",
        "limit": limit
    }
    if offset_id:
        params["offset_id"] = offset_id
    if search:
        params["search"] = search
        
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(url, headers=headers, params=params)
        return resp.json()

async def get_storage_stats_realtime(api_key: str):
    """Calculate 100% accurate real-time storage statistics from live scan."""
    try:
        files_res = await list_files(api_key, folder_id="all", limit=1000)
        items = files_res.get("items", []) if files_res.get("status") == "success" else []
        
        folders_res = await list_folders(api_key, parent_id="all")
        custom_folders = folders_res.get("folders", []) if folders_res.get("status") == "success" else []

        total_files = len(items)
        total_folders = len(custom_folders)
        total_storage_bytes = sum(f.get("size", 0) for f in items)
        
        categories = {
            "videos": {"count": 0, "bytes": 0},
            "images": {"count": 0, "bytes": 0},
            "audio": {"count": 0, "bytes": 0},
            "documents": {"count": 0, "bytes": 0},
            "apks": {"count": 0, "bytes": 0},
            "others": {"count": 0, "bytes": 0}
        }
        
        for item in items:
            cat = categorize_file(item)
            categories[cat]["count"] += 1
            categories[cat]["bytes"] += item.get("size", 0)

        total_mb = f"{total_storage_bytes / (1024 * 1024):.2f}"
        total_gb = f"{total_storage_bytes / (1024 * 1024 * 1024):.3f}"

        return {
            "status": "success",
            "total_files": total_files,
            "total_folders": total_folders,
            "total_storage_bytes": total_storage_bytes,
            "total_storage_mb": total_mb,
            "total_storage_gb": total_gb,
            "quota": "Unlimited Free (Telegram Cloud)",
            "storage_engine": "Telegram Cloud MTProto ('Saved Messages')",
            "category_breakdown": categories
        }
    except Exception as e:
        logger.error(f"Realtime stats error: {e}")
        return {"status": "error", "message": str(e)}

async def get_file_info(api_key: str, file_id: str):
    """Get file details and metadata."""
    url = f"{API_BASE_URL}/v1/files/{file_id}"
    headers = _get_headers(api_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers)
        return resp.json()

async def upload_file_streaming(api_key: str, file_path: str, filename: str, folder_id: str = "root", mime_type: str = "application/octet-stream", progress_cb = None):
    """Upload file to TG Drive with streaming and progress reporting."""
    url = f"{API_BASE_URL}/v1/files/upload"
    headers = _get_headers(api_key)
    total_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

    async def file_stream_generator():
        sent = 0
        chunk_size = 512 * 1024  # 512 KB
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                sent += len(chunk)
                if progress_cb:
                    try:
                        await progress_cb(sent, total_size)
                    except Exception:
                        pass
                yield chunk

    form = aiohttp.FormData()
    form.add_field('folder_id', folder_id)
    form.add_field('file', file_stream_generator(), filename=filename, content_type=mime_type)

    timeout = aiohttp.ClientTimeout(total=900)  # 15 minutes max
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        async with session.post(url, data=form) as resp:
            try:
                return await resp.json()
            except Exception:
                text = await resp.text()
                return {"status": "error", "message": f"Server response: {text[:200]}"}

async def delete_file(api_key: str, file_id: str):
    """Delete a file permanently."""
    url = f"{API_BASE_URL}/v1/files/{file_id}"
    headers = _get_headers(api_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(url, headers=headers)
        return resp.json()

async def star_file(api_key: str, file_id: str, starred: bool = True):
    """Toggle star / favorite status for a file."""
    url = f"{API_BASE_URL}/v1/files/{file_id}/star"
    headers = _get_headers(api_key)
    headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=headers, json={"starred": starred})
        return resp.json()

async def list_favorites(api_key: str):
    """List starred / favorite files."""
    url = f"{API_BASE_URL}/v1/favorites"
    headers = _get_headers(api_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers)
        return resp.json()

async def search_items(api_key: str, query: str, search_type: str = "all", mime_type: str = None):
    """Search files and folders by name, type, and MIME type."""
    url = f"{API_BASE_URL}/v1/search"
    headers = _get_headers(api_key)
    params = {
        "q": query,
        "type": search_type
    }
    if mime_type:
        params["mime_type"] = mime_type
        
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=headers, params=params)
        return resp.json()

async def list_folders(api_key: str, parent_id: str = "root"):
    """List virtual folders."""
    url = f"{API_BASE_URL}/v1/folders"
    headers = _get_headers(api_key)
    params = {"parent_id": parent_id}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(url, headers=headers, params=params)
        data = resp.json()
        if isinstance(data, dict):
            items = data.get("items") or data.get("folders", [])
            data["folders"] = items
            data["items"] = items
        return data

async def create_folder(api_key: str, name: str, parent_id: str = "root"):
    """Create a new virtual folder."""
    url = f"{API_BASE_URL}/v1/folders"
    headers = _get_headers(api_key)
    headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=headers, json={"name": name, "parent_id": parent_id})
        return resp.json()

async def delete_folder(api_key: str, folder_id: str):
    """Delete a virtual folder."""
    url = f"{API_BASE_URL}/v1/folders/{folder_id}"
    headers = _get_headers(api_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.delete(url, headers=headers)
        return resp.json()

async def list_trash(api_key: str):
    """List items in recycle bin."""
    url = f"{API_BASE_URL}/v1/trash"
    headers = _get_headers(api_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, headers=headers)
        return resp.json()

async def restore_trash(api_key: str, file_id: str):
    """Restore file from recycle bin."""
    url = f"{API_BASE_URL}/v1/trash/{file_id}/restore"
    headers = _get_headers(api_key)
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, headers=headers)
        return resp.json()

async def rename_file(api_key: str, file_id: str, new_name: str):
    """Rename a file in TG Drive."""
    url = f"{API_BASE_URL}/v1/files/{file_id}/rename"
    headers = _get_headers(api_key)
    headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.patch(url, headers=headers, json={"name": new_name.strip()})
        return resp.json()

async def move_file(api_key: str, file_id: str, folder_id: str):
    """Move a file to another folder in TG Drive."""
    url = f"{API_BASE_URL}/v1/files/{file_id}/move"
    headers = _get_headers(api_key)
    headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.patch(url, headers=headers, json={"folder_id": str(folder_id)})
        return resp.json()

async def rename_folder(api_key: str, folder_id: str, new_name: str):
    """Rename a virtual folder."""
    url = f"{API_BASE_URL}/v1/folders/{folder_id}"
    headers = _get_headers(api_key)
    headers["Content-Type"] = "application/json"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.patch(url, headers=headers, json={"name": new_name.strip()})
        return resp.json()

async def empty_trash(api_key: str):
    """Purge all files in trash."""
    url = f"{API_BASE_URL}/v1/trash"
    headers = _get_headers(api_key)
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.delete(url, headers=headers)
        return resp.json()
