import sqlite3
import json
import logging
from config import DATABASE_PATH

logger = logging.getLogger(__name__)

def get_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initialize database tables."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            api_key TEXT NOT NULL,
            username TEXT,
            first_name TEXT,
            current_folder_id TEXT DEFAULT 'root',
            current_folder_name TEXT DEFAULT 'Root (Saved Messages)',
            state TEXT,
            state_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS folders (
            folder_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            parent_id TEXT DEFAULT 'root',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS file_folders (
            file_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            folder_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Add column if not exists (for existing database)
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN current_folder_name TEXT DEFAULT 'Root (Saved Messages)'")
    except Exception:
        pass

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully.")

def get_user(user_id: int):
    """Fetch user record by user_id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def get_user_api_key(user_id: int):
    """Get the saved API key for a user."""
    user = get_user(user_id)
    if user and user.get("api_key"):
        return user["api_key"]
    return None

def set_user_api_key(user_id: int, api_key: str, username: str = None, first_name: str = None):
    """Save or update user API key."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, api_key, username, first_name, last_active)
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            api_key = excluded.api_key,
            username = COALESCE(excluded.username, users.username),
            first_name = COALESCE(excluded.first_name, users.first_name),
            last_active = CURRENT_TIMESTAMP,
            state = NULL,
            state_data = NULL
    """, (user_id, api_key.strip(), username, first_name))
    conn.commit()
    conn.close()

def delete_user_api_key(user_id: int):
    """Remove user's API key (logout)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def sync_user_folders(user_id: int, folders_list: list):
    """Clean and sync local folders with live folders from Telegram Saved Messages API."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM folders WHERE user_id = ?", (user_id,))
    for f in folders_list:
        f_id = str(f.get("id") or f.get("message_id"))
        f_name = f.get("name")
        p_id = str(f.get("parentId") or f.get("parent_id") or "root")
        if f_id and f_name:
            cursor.execute("""
                INSERT OR REPLACE INTO folders (folder_id, user_id, name, parent_id)
                VALUES (?, ?, ?, ?)
            """, (f_id, user_id, f_name.strip(), p_id))
    conn.commit()
    conn.close()

def add_user_folder(user_id: int, folder_id: str, name: str, parent_id: str = "root"):
    """Add or update a custom folder in the database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO folders (folder_id, user_id, name, parent_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(folder_id) DO UPDATE SET name = excluded.name, parent_id = excluded.parent_id
    """, (str(folder_id), user_id, name.strip(), str(parent_id)))
    conn.commit()
    conn.close()

def get_user_folders(user_id: int, parent_id: str = "root"):
    """Get all custom folders for a user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT folder_id as id, name, parent_id, created_at FROM folders 
        WHERE user_id = ?
        ORDER BY created_at ASC
    """, (user_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_folder_by_id(user_id: int, folder_id: str):
    """Retrieve folder metadata by folder_id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT folder_id as id, name, parent_id, created_at FROM folders 
        WHERE user_id = ? AND folder_id = ?
    """, (user_id, str(folder_id)))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None

def delete_user_folder(user_id: int, folder_id: str):
    """Delete folder and any associated file mapping from database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM folders WHERE user_id = ? AND folder_id = ?", (user_id, str(folder_id)))
    cursor.execute("DELETE FROM file_folders WHERE user_id = ? AND folder_id = ?", (user_id, str(folder_id)))
    conn.commit()
    conn.close()

def set_file_folder(user_id: int, file_id: str, folder_id: str):
    """Link a file to a specific folder in SQLite."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO file_folders (file_id, user_id, folder_id)
        VALUES (?, ?, ?)
        ON CONFLICT(file_id) DO UPDATE SET folder_id = excluded.folder_id
    """, (str(file_id), user_id, str(folder_id)))
    conn.commit()
    conn.close()

def get_files_in_folder(user_id: int, folder_id: str):
    """Get list of file IDs assigned to a folder."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT file_id FROM file_folders WHERE user_id = ? AND folder_id = ?", (user_id, str(folder_id)))
    rows = cursor.fetchall()
    conn.close()
    return [str(r["file_id"]) for r in rows]

def get_user_folder(user_id: int):
    """Get user's current folder ID and Name. Returns (folder_id, folder_name)."""
    user = get_user(user_id)
    if user:
        f_id = user.get("current_folder_id") or "root"
        f_name = user.get("current_folder_name") or ("Root (Saved Messages)" if f_id == "root" else f_id)
        return f_id, f_name
    return "root", "Root (Saved Messages)"

def set_user_folder(user_id: int, folder_id: str, folder_name: str = "Root (Saved Messages)"):
    """Set user's active default target upload folder."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users 
        SET current_folder_id = ?, current_folder_name = ?, last_active = CURRENT_TIMESTAMP 
        WHERE user_id = ?
    """, (str(folder_id), folder_name, user_id))
    conn.commit()
    conn.close()

def reset_user_folder(user_id: int):
    """Reset target folder back to root."""
    set_user_folder(user_id, "root", "Root (Saved Messages)")

def set_user_state(user_id: int, state: str, state_data: dict = None):
    """Set a conversational state for user."""
    conn = get_connection()
    cursor = conn.cursor()
    data_str = json.dumps(state_data) if state_data else None
    cursor.execute("""
        UPDATE users SET state = ?, state_data = ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?
    """, (state, data_str, user_id))
    if cursor.rowcount == 0:
        cursor.execute("""
            INSERT INTO users (user_id, api_key, state, state_data) VALUES (?, '', ?, ?)
        """, (user_id, state, data_str))
    conn.commit()
    conn.close()

def get_user_state(user_id: int):
    """Returns (state: str or None, state_data: dict or None)."""
    user = get_user(user_id)
    if not user:
        return None, None
    state = user.get("state")
    data_str = user.get("state_data")
    state_data = json.loads(data_str) if data_str else None
    return state, state_data

def clear_user_state(user_id: int):
    """Clear any active state for the user."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET state = NULL, state_data = NULL WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def update_user_activity(user_id: int, username: str = None, first_name: str = None):
    """Update last active timestamp."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE users SET
            username = COALESCE(?, username),
            first_name = COALESCE(?, first_name),
            last_active = CURRENT_TIMESTAMP
        WHERE user_id = ?
    """, (username, first_name, user_id))
    conn.commit()
    conn.close()
