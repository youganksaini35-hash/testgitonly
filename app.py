import os
import sys
import time
import json
import psutil
import logging
import signal
import threading
import subprocess
import traceback
from datetime import datetime
import requests

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("TGBotController")

# ---------------------------------------------------------------------------
# Configuration & Environment Variables
# ---------------------------------------------------------------------------
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "8947225372:AAH7ubfHB-KyelruqrjoIgrgCeAZj_XDWYE")
GH_PAT = os.environ.get("GH_PAT", os.environ.get("GITHUB_TOKEN", ""))
REPO = os.environ.get("GITHUB_REPOSITORY", "Saini920/testgitonly")
RUN_ID = os.environ.get("GITHUB_RUN_ID", "local-dev")
WORKFLOW_FILE = os.environ.get("WORKFLOW_FILE", "server.yml")
WORKFLOW_REF = os.environ.get("WORKFLOW_REF", "main")

# Default run duration: 5.5 hours (19800 seconds)
RUN_DURATION_SECONDS = int(os.environ.get("RUN_DURATION_SECONDS", "19800"))
START_TIME = time.time()
IS_RUNNING = True

# Path to state / config
WORKSPACE_DIR = os.getcwd()
CONFIG_FILE = os.path.join(WORKSPACE_DIR, "bot_config.json")

# Process state
child_process = None
child_process_name = None
child_process_start_time = None
child_logs = []
LOG_BUFFER_MAX = 200

# ---------------------------------------------------------------------------
# Telegram API Helper Functions
# ---------------------------------------------------------------------------
TG_BASE_URL = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"admin_id": None, "auto_run_file": "bot.py"}

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving config: {e}")

config = load_config()

def send_tg_message(chat_id, text, parse_mode="HTML"):
    """Send message to Telegram with auto-chunking for long text."""
    url = f"{TG_BASE_URL}/sendMessage"
    
    # Split text if exceeding Telegram's 4000 character limit
    max_len = 3900
    chunks = [text[i:i+max_len] for i in range(0, len(text), max_len)]
    
    for chunk in chunks:
        try:
            payload = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode
            }
            requests.post(url, json=payload, timeout=10)
        except Exception as e:
            # Fallback without parse mode in case of formatting error
            try:
                payload = {"chat_id": chat_id, "text": chunk}
                requests.post(url, json=payload, timeout=10)
            except Exception as e2:
                logger.error(f"Failed to send Telegram message: {e2}")

def send_tg_document(chat_id, filepath, caption=""):
    url = f"{TG_BASE_URL}/sendDocument"
    try:
        with open(filepath, "rb") as f:
            files = {"document": f}
            data = {"chat_id": chat_id, "caption": caption}
            requests.post(url, data=data, files=files, timeout=20)
    except Exception as e:
        send_tg_message(chat_id, f"❌ Failed to send document: {e}", parse_mode=None)

def download_tg_file(file_id, destination_path):
    """Download a file uploaded to Telegram."""
    try:
        # Step 1: Get file path
        url = f"{TG_BASE_URL}/getFile?file_id={file_id}"
        resp = requests.get(url, timeout=10).json()
        if not resp.get("ok"):
            return False, "Could not get file path from Telegram."
        
        file_path = resp["result"]["file_path"]
        download_url = f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{file_path}"
        
        # Step 2: Download file content
        file_resp = requests.get(download_url, timeout=30)
        if file_resp.status_code == 200:
            with open(destination_path, "wb") as f:
                f.write(file_resp.content)
            return True, None
        return False, f"HTTP Error {file_resp.status_code}"
    except Exception as e:
        return False, str(e)

# ---------------------------------------------------------------------------
# GitHub Git Auto-Sync (Sync changes back to GitHub Repo across handoffs)
# ---------------------------------------------------------------------------
def git_sync_to_github(commit_message="Update from Telegram Bot Controller"):
    """Commits and pushes local workspace changes to GitHub."""
    if not GH_PAT or not REPO:
        return False, "GH_PAT or REPO not set"
    
    try:
        remote_url = f"https://{GH_PAT}@github.com/{REPO}.git"
        subprocess.run(["git", "config", "user.name", "RelayBotController"], check=True)
        subprocess.run(["git", "config", "user.email", "bot@relay.local"], check=True)
        subprocess.run(["git", "add", "."], check=True)
        
        # Check if there are changes to commit
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip():
            return True, "No changes to sync"
        
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        push_res = subprocess.run(["git", "push", remote_url, "main"], capture_output=True, text=True)
        if push_res.returncode == 0:
            return True, "Synced to GitHub successfully!"
        return False, push_res.stderr
    except Exception as e:
        return False, str(e)

# ---------------------------------------------------------------------------
# Child Process Management (Run, Stop, Restart Python Scripts)
# ---------------------------------------------------------------------------
def append_log(line):
    global child_logs
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{timestamp}] {line}"
    child_logs.append(formatted)
    if len(child_logs) > LOG_BUFFER_MAX:
        child_logs.pop(0)

def log_stream_reader(pipe):
    """Background thread to read output from child process."""
    try:
        for line in iter(pipe.readline, ''):
            if not line:
                break
            clean_line = line.rstrip()
            append_log(clean_line)
            logger.info(f"[ChildApp] {clean_line}")
        pipe.close()
    except Exception:
        pass

def start_child_app(filename="bot.py"):
    global child_process, child_process_name, child_process_start_time
    
    if not os.path.exists(filename):
        return False, f"File <code>{filename}</code> not found in workspace!"
    
    # If already running, stop first
    if child_process and child_process.poll() is None:
        stop_child_app()
    
    try:
        cmd = [sys.executable, "-u", filename]
        child_process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=WORKSPACE_DIR
        )
        child_process_name = filename
        child_process_start_time = time.time()
        
        config["auto_run_file"] = filename
        save_config(config)
        
        # Start log reader thread
        threading.Thread(target=log_stream_reader, args=(child_process.stdout,), daemon=True).start()
        
        return True, f"✅ Started <code>{filename}</code> (PID: {child_process.pid})"
    except Exception as e:
        return False, f"❌ Failed to start: {e}"

def stop_child_app():
    global child_process, child_process_name, child_process_start_time
    if child_process and child_process.poll() is None:
        name = child_process_name
        try:
            child_process.terminate()
            child_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child_process.kill()
        
        child_process = None
        child_process_name = None
        child_process_start_time = None
        return True, f"🛑 Stopped <code>{name}</code>"
    return False, "No child script is currently running."

def restart_child_app():
    global child_process_name
    file_to_run = child_process_name or config.get("auto_run_file", "bot.py")
    stop_child_app()
    time.sleep(1)
    return start_child_app(file_to_run)

# ---------------------------------------------------------------------------
# Self-Trigger: Next Runner Launch (Relay Handoff)
# ---------------------------------------------------------------------------
def trigger_next_runner():
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    payload = {"ref": WORKFLOW_REF}
    headers = {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        return resp.status_code == 204
    except Exception as e:
        logger.error(f"Error triggering workflow: {e}")
        return False

# ---------------------------------------------------------------------------
# Telegram Command Handlers
# ---------------------------------------------------------------------------
def handle_command(chat_id, user_id, text):
    global config
    
    # Authenticate / Claim Admin
    if config["admin_id"] is None:
        config["admin_id"] = user_id
        save_config(config)
        send_tg_message(chat_id, f"👑 <b>Admin Registered!</b> Your User ID: <code>{user_id}</code> is now the authorized master of this server.")
    
    if user_id != config["admin_id"]:
        send_tg_message(chat_id, "⛔ <b>Access Denied:</b> You are not authorized to control this runner.")
        return

    parts = text.strip().split()
    cmd = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []

    # 1. /start or /help
    if cmd in ["/start", "/help"]:
        help_text = (
            "⚡ <b>24/7 Cloud Relay Bot Controller</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>🎮 Process Controls:</b>\n"
            "• <code>/run &lt;file.py&gt;</code> - Start a python script\n"
            "• <code>/stop</code> - Stop the running script\n"
            "• <code>/restart</code> - Restart the current script\n"
            "• <code>/status</code> - Show runner & process status\n"
            "• <code>/logs [n]</code> - View last N lines of logs (default: 25)\n\n"
            "<b>📁 File & Code Management:</b>\n"
            "• <i>Just send any .py or text file in chat to upload!</i>\n"
            "• <i>Send requirements.txt to auto-install dependencies!</i>\n"
            "• <code>/files</code> - List all files in workspace\n"
            "• <code>/download &lt;file&gt;</code> - Download file to Telegram\n"
            "• <code>/delete &lt;file&gt;</code> - Delete a file\n"
            "• <code>/sync</code> - Save & push changes to GitHub\n\n"
            "<b>💻 Shell & Package Manager:</b>\n"
            "• <code>/pip &lt;package&gt;</code> - Install Python package\n"
            "• <code>/sh &lt;command&gt;</code> - Execute Linux shell command\n"
            "• <code>/stats</code> - Server CPU, RAM & Uptime"
        )
        send_tg_message(chat_id, help_text)

    # 2. /status
    elif cmd == "/status":
        uptime_sec = int(time.time() - START_TIME)
        hours, remainder = divmod(uptime_sec, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        is_child_alive = child_process and child_process.poll() is None
        child_status = "🟢 Running" if is_child_alive else "🔴 Stopped"
        
        child_uptime_str = "N/A"
        if is_child_alive and child_process_start_time:
            cu_sec = int(time.time() - child_process_start_time)
            ch, cr = divmod(cu_sec, 3600)
            cm, cs = divmod(cr, 60)
            child_uptime_str = f"{ch}h {cm}m {cs}s"

        ram = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=None)

        status_msg = (
            "📊 <b>System & Runner Status</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Relay Runner:</b> 🟢 Online (Run #{RUN_ID})\n"
            f"• <b>Runner Uptime:</b> {hours}h {minutes}m {seconds}s\n"
            f"• <b>Active Script:</b> <code>{child_process_name or 'None'}</code>\n"
            f"• <b>Script State:</b> {child_status}\n"
            f"• <b>Script Uptime:</b> {child_uptime_str}\n"
            f"• <b>RAM Usage:</b> {ram.percent}% ({ram.used // (1024*1024)}MB / {ram.total // (1024*1024)}MB)\n"
            f"• <b>CPU Load:</b> {cpu}%\n"
            f"• <b>Auto-Sync Repo:</b> <code>{REPO}</code>"
        )
        send_tg_message(chat_id, status_msg)

    # 3. /run <filename>
    elif cmd in ["/run", "/start_app"]:
        filename = args[0] if args else config.get("auto_run_file", "bot.py")
        success, msg = start_child_app(filename)
        send_tg_message(chat_id, msg)

    # 4. /stop
    elif cmd in ["/stop", "/stop_app"]:
        success, msg = stop_child_app()
        send_tg_message(chat_id, msg)

    # 5. /restart
    elif cmd == "/restart":
        send_tg_message(chat_id, "🔄 Restarting application...")
        success, msg = restart_child_app()
        send_tg_message(chat_id, msg)

    # 6. /logs
    elif cmd == "/logs":
        lines_count = int(args[0]) if args and args[0].isdigit() else 25
        if not child_logs:
            send_tg_message(chat_id, "ℹ️ No logs captured yet.")
        else:
            selected_logs = "\n".join(child_logs[-lines_count:])
            send_tg_message(chat_id, f"📋 <b>Last {min(lines_count, len(child_logs))} Log Lines:</b>\n<pre>{selected_logs}</pre>")

    # 7. /files
    elif cmd == "/files":
        items = os.listdir(WORKSPACE_DIR)
        file_list = []
        for it in sorted(items):
            if it.startswith(".git"):
                continue
            path = os.path.join(WORKSPACE_DIR, it)
            if os.path.isdir(path):
                file_list.append(f"📁 <b>{it}/</b>")
            else:
                sz = os.path.getsize(path)
                file_list.append(f"📄 <code>{it}</code> ({sz} bytes)")
        
        msg = "📂 <b>Workspace Files:</b>\n" + ("\n".join(file_list) if file_list else "<i>Empty directory</i>")
        send_tg_message(chat_id, msg)

    # 8. /download <filename>
    elif cmd == "/download":
        if not args:
            send_tg_message(chat_id, "Usage: <code>/download filename.py</code>")
            return
        fname = args[0]
        fpath = os.path.join(WORKSPACE_DIR, fname)
        if os.path.exists(fpath) and os.path.isfile(fpath):
            send_tg_document(chat_id, fpath, caption=f"📄 {fname}")
        else:
            send_tg_message(chat_id, f"❌ File <code>{fname}</code> does not exist.")

    # 9. /delete <filename>
    elif cmd == "/delete":
        if not args:
            send_tg_message(chat_id, "Usage: <code>/delete filename.py</code>")
            return
        fname = args[0]
        fpath = os.path.join(WORKSPACE_DIR, fname)
        if os.path.exists(fpath):
            os.remove(fpath)
            git_sync_to_github(f"Delete {fname} via Telegram")
            send_tg_message(chat_id, f"🗑️ Deleted <code>{fname}</code> and synced to GitHub.")
        else:
            send_tg_message(chat_id, f"❌ File <code>{fname}</code> not found.")

    # 10. /pip <package>
    elif cmd == "/pip":
        if not args:
            send_tg_message(chat_id, "Usage: <code>/pip install pyTelegramBotAPI</code>")
            return
        pip_cmd = [sys.executable, "-m", "pip"] + args
        send_tg_message(chat_id, f"⏳ Running: <code>pip {' '.join(args)}</code>...")
        res = subprocess.run(pip_cmd, capture_output=True, text=True)
        out = (res.stdout + "\n" + res.stderr).strip()
        send_tg_message(chat_id, f"<pre>{out[-3000:]}</pre>")

    # 11. /sh <command>
    elif cmd in ["/sh", "/exec"]:
        if not args:
            send_tg_message(chat_id, "Usage: <code>/sh ls -la</code>")
            return
        raw_cmd = " ".join(args)
        send_tg_message(chat_id, f"⚡ Executing: <code>{raw_cmd}</code>...")
        res = subprocess.run(raw_cmd, shell=True, capture_output=True, text=True, cwd=WORKSPACE_DIR)
        out = (res.stdout + "\n" + res.stderr).strip() or "<i>(No output)</i>"
        send_tg_message(chat_id, f"<pre>{out[-3000:]}</pre>")

    # 12. /sync
    elif cmd == "/sync":
        send_tg_message(chat_id, "⏳ Syncing all changes to GitHub repository...")
        ok, msg = git_sync_to_github()
        send_tg_message(chat_id, f"{'✅' if ok else '❌'} {msg}")

    # 13. /stats
    elif cmd == "/stats":
        ram = psutil.virtual_memory()
        cpu = psutil.cpu_percent(interval=1)
        disk = psutil.disk_usage('/')
        stats_text = (
            "💻 <b>System Statistics</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>CPU Usage:</b> {cpu}%\n"
            f"• <b>RAM:</b> {ram.percent}% ({ram.used // (1024*1024)}MB / {ram.total // (1024*1024)}MB)\n"
            f"• <b>Disk:</b> {disk.percent}% ({disk.used // (1024*1024*1024)}GB / {disk.total // (1024*1024*1024)}GB)\n"
            f"• <b>Python Version:</b> {sys.version.split()[0]}"
        )
        send_tg_message(chat_id, stats_text)

    else:
        send_tg_message(chat_id, f"❓ Unknown command <code>{cmd}</code>. Type <code>/help</code> for available commands.")

def handle_document(chat_id, user_id, doc):
    if user_id != config.get("admin_id") and config.get("admin_id") is not None:
        send_tg_message(chat_id, "⛔ Unauthorized.")
        return
    
    file_id = doc.get("file_id")
    file_name = doc.get("file_name", f"file_{int(time.time())}")
    dest_path = os.path.join(WORKSPACE_DIR, file_name)
    
    send_tg_message(chat_id, f"📥 Receiving <code>{file_name}</code>...")
    ok, err = download_tg_file(file_id, dest_path)
    
    if not ok:
        send_tg_message(chat_id, f"❌ Download failed: {err}")
        return
    
    # Auto-commit to GitHub
    git_sync_to_github(f"Upload {file_name} from Telegram")
    
    # If requirements.txt, auto install
    if file_name == "requirements.txt":
        send_tg_message(chat_id, "📦 <code>requirements.txt</code> detected! Auto-installing dependencies...")
        res = subprocess.run([sys.executable, "-m", "pip", "install", "-r", dest_path], capture_output=True, text=True)
        send_tg_message(chat_id, f"✅ Dependencies installed:\n<pre>{res.stdout[-2000:]}</pre>")
    elif file_name.endswith(".py"):
        send_tg_message(chat_id, f"✅ <b>{file_name}</b> saved and synced to GitHub!\n\nTo run it now, send:\n<code>/run {file_name}</code>")
    else:
        send_tg_message(chat_id, f"✅ Saved <code>{file_name}</code> to workspace and synced to GitHub.")

# ---------------------------------------------------------------------------
# Telegram Long-Polling Worker
# ---------------------------------------------------------------------------
def telegram_polling_loop():
    logger.info("🤖 Telegram Polling loop started...")
    offset = 0
    
    while IS_RUNNING:
        try:
            url = f"{TG_BASE_URL}/getUpdates?offset={offset}&timeout=20"
            resp = requests.get(url, timeout=25)
            
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        msg = update.get("message", {})
                        chat_id = msg.get("chat", {}).get("id")
                        user_id = msg.get("from", {}).get("id")
                        
                        if not chat_id or not user_id:
                            continue
                        
                        # Handle text message
                        if "text" in msg:
                            handle_command(chat_id, user_id, msg["text"])
                        
                        # Handle uploaded document / script
                        elif "document" in msg:
                            handle_document(chat_id, user_id, msg["document"])
            time.sleep(1)
        except Exception as e:
            logger.error(f"Error in TG polling loop: {e}")
            time.sleep(3)

# ---------------------------------------------------------------------------
# Main Orchestrator & Auto-Runner
# ---------------------------------------------------------------------------
def main():
    logger.info("=" * 60)
    logger.info(f"🚀 Telegram Relay Server Controller Initialized [Run #{RUN_ID}]")
    logger.info("=" * 60)
    
    # Notify Admin if admin_id is already known
    if config.get("admin_id"):
        send_tg_message(
            config["admin_id"],
            f"🟢 <b>Relay Runner #{RUN_ID} is LIVE!</b>\n"
            f"GitHub Runner environment ready. Type <code>/status</code> or <code>/help</code> to manage."
        )
    
    # Auto-start previously configured script if it exists
    auto_file = config.get("auto_run_file", "bot.py")
    if os.path.exists(os.path.join(WORKSPACE_DIR, auto_file)):
        logger.info(f"Auto-starting {auto_file}...")
        start_child_app(auto_file)
        if config.get("admin_id"):
            send_tg_message(config["admin_id"], f"🚀 Auto-started <code>{auto_file}</code>.")

    # Start Telegram polling thread
    tg_thread = threading.Thread(target=telegram_polling_loop, daemon=True, name="TGPolling")
    tg_thread.start()
    
    # Main duration watcher loop (5.5 hours)
    while IS_RUNNING:
        elapsed = time.time() - START_TIME
        if elapsed >= RUN_DURATION_SECONDS:
            logger.info(f"⏳ 5.5 Hours reached ({RUN_DURATION_SECONDS}s). Performing Handoff...")
            break
        time.sleep(5)
    
    # --- HANDOFF SEQUENCE ---
    if config.get("admin_id"):
        send_tg_message(
            config["admin_id"],
            "🔄 <b>5.5 Hours Relay Limit Reached:</b>\n"
            "Backing up files to GitHub and launching next runner..."
        )
    
    # 1. Stop child process
    stop_child_app()
    
    # 2. Sync all local files & state to GitHub
    git_sync_to_github("Auto-backup before Relay Handoff")
    
    # 3. Trigger next runner via GitHub Actions API
    logger.info("Triggering successor workflow via API...")
    trigger_next_runner()
    
    # 4. Safety buffer
    logger.info("Holding safety window (15s)...")
    time.sleep(15)
    
    logger.info("Handoff sequence complete. Exiting current runner.")

if __name__ == "__main__":
    main()
