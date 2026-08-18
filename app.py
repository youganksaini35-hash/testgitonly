import os
import sys
import time
import json
import html
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
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
GH_PAT = os.environ.get("GH_PAT", os.environ.get("GITHUB_TOKEN", ""))
REPO = os.environ.get("GITHUB_REPOSITORY", "Saini920/testgitonly")
RUN_ID = os.environ.get("GITHUB_RUN_ID", "local-dev")
WORKFLOW_FILE = os.environ.get("WORKFLOW_FILE", "server.yml")
WORKFLOW_REF = os.environ.get("WORKFLOW_REF", "main")

# Default run duration: 5.5 hours (19800 seconds)
RUN_DURATION_SECONDS = int(os.environ.get("RUN_DURATION_SECONDS", "19800"))
START_TIME = time.time()
IS_RUNNING = True

# Workspace & Config
WORKSPACE_DIR = os.getcwd()
SCRIPTS_DIR = os.path.join(WORKSPACE_DIR, "scripts")
os.makedirs(SCRIPTS_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(WORKSPACE_DIR, "bot_config.json")

# State & Processes
child_process = None
child_process_name = None
child_process_start_time = None
child_logs = []
LOG_BUFFER_MAX = 200

# User conversation states (for interactive step-by-step inputs)
user_states = {}

# ---------------------------------------------------------------------------
# Telegram API Helpers (Buttons & Messages)
# ---------------------------------------------------------------------------
TG_BASE_URL = f"https://api.telegram.org/bot{TG_BOT_TOKEN}"

def load_config():
    default_cfg = {
        "admin_ids": [7249511572, 7251749429],
        "auto_run_file": "bot.py"
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                if "admin_id" in data and "admin_ids" not in data:
                    data["admin_ids"] = [data["admin_id"]] if data["admin_id"] else []
                if "admin_ids" not in data:
                    data["admin_ids"] = [7249511572, 7251749429]
                if 7251749429 not in data["admin_ids"]:
                    data["admin_ids"].append(7251749429)
                return data
        except Exception:
            pass
    return default_cfg

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving config: {e}")

config = load_config()

def is_admin(user_id):
    admin_list = config.get("admin_ids", [7249511572, 7251749429])
    if not admin_list:
        return True
    return user_id in admin_list

def notify_all_admins(text, reply_markup=None):
    for a_id in config.get("admin_ids", [7249511572, 7251749429]):
        send_tg_message(a_id, text, reply_markup=reply_markup)

def send_tg_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    url = f"{TG_BASE_URL}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    
    try:
        resp = requests.post(url, json=payload, timeout=10).json()
        if not resp.get("ok"):
            # Fallback without parse_mode in case of HTML entity error
            payload.pop("parse_mode", None)
            return requests.post(url, json=payload, timeout=10).json()
        return resp
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return None

def edit_tg_message(chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
    url = f"{TG_BASE_URL}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": parse_mode
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(url, json=payload, timeout=10).json()
        if not resp.get("ok"):
            err_desc = resp.get("description", "")
            if "message is not modified" in err_desc:
                return resp
            # Fallback without parse_mode in case of HTML entity parsing issue
            payload.pop("parse_mode", None)
            return requests.post(url, json=payload, timeout=10).json()
        return resp
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")
        return None

def answer_callback(callback_query_id, text=None, show_alert=False):
    url = f"{TG_BASE_URL}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception:
        pass

def send_tg_document(chat_id, filepath, caption=""):
    url = f"{TG_BASE_URL}/sendDocument"
    try:
        with open(filepath, "rb") as f:
            files = {"document": f}
            data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
            requests.post(url, data=data, files=files, timeout=30)
    except Exception as e:
        send_tg_message(chat_id, f"❌ Failed to send document: {e}")

def download_tg_file(file_id, destination_path):
    try:
        url = f"{TG_BASE_URL}/getFile?file_id={file_id}"
        resp = requests.get(url, timeout=10).json()
        if not resp.get("ok"):
            return False, "Could not retrieve file path from Telegram."
        
        file_path = resp["result"]["file_path"]
        download_url = f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{file_path}"
        
        file_resp = requests.get(download_url, timeout=45)
        if file_resp.status_code == 200:
            with open(destination_path, "wb") as f:
                f.write(file_resp.content)
            return True, None
        return False, f"HTTP Error {file_resp.status_code}"
    except Exception as e:
        return False, str(e)

# ---------------------------------------------------------------------------
# GitHub Git Auto-Sync
# ---------------------------------------------------------------------------
def git_sync_to_github(commit_message="Update via Telegram Controller"):
    if not GH_PAT or not REPO:
        return False, "GitHub Token or Repo not set"
    
    try:
        remote_url = f"https://{GH_PAT}@github.com/{REPO}.git"
        subprocess.run(["git", "config", "user.name", "TelegramController"], check=True)
        subprocess.run(["git", "config", "user.email", "bot@controller.local"], check=True)
        subprocess.run(["git", "add", "."], check=True)
        
        status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if not status.stdout.strip():
            return True, "All files already up to date."
        
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        push_res = subprocess.run(["git", "push", remote_url, "main"], capture_output=True, text=True)
        if push_res.returncode == 0:
            return True, "Successfully synced to GitHub!"
        return False, push_res.stderr
    except Exception as e:
        return False, str(e)

# ---------------------------------------------------------------------------
# Process Manager (Run, Stop, Restart Scripts)
# ---------------------------------------------------------------------------
def append_log(line):
    global child_logs
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{timestamp}] {line}"
    child_logs.append(formatted)
    if len(child_logs) > LOG_BUFFER_MAX:
        child_logs.pop(0)

def log_stream_reader(pipe):
    try:
        for line in iter(pipe.readline, ''):
            if not line:
                break
            clean = line.rstrip()
            append_log(clean)
            logger.info(f"[ChildApp] {clean}")
        pipe.close()
    except Exception:
        pass

def child_watchdog(proc, fname):
    """Watches the running child process and sends alert if it exits or crashes."""
    ret = proc.wait()
    global child_process, child_process_name, child_process_start_time
    
    # Only act if this is still the registered active process
    if child_process == proc:
        child_process = None
        child_process_name = None
        child_process_start_time = None
        
        recent_err = "\n".join(child_logs[-15:]) if child_logs else "(No output recorded)"
        
        if config.get("admin_ids"):
            if ret != 0:
                alert_text = (
                    f"⚠️ <b>Script Crashed / Exited!</b>\n"
                    f"📁 <b>File:</b> <code>{fname}</code>\n"
                    f"🔴 <b>Exit Code:</b> <code>{ret}</code>\n\n"
                    f"<b>Error Traceback:</b>\n"
                    f"<pre>{recent_err[-3000:]}</pre>\n\n"
                    f"💡 <i>Tip: Agar koi module missing hai toh package install karein.</i>"
                )
                markup = {
                    "inline_keyboard": [
                        [{"text": "📦 Install Missing Package", "callback_data": "menu_pip_prompt"}],
                        [{"text": "🔄 Try Restarting", "callback_data": f"exec_run_{fname}"}],
                        [{"text": "🔙 Main Menu", "callback_data": "menu_main"}]
                    ]
                }
            else:
                alert_text = (
                    f"ℹ️ <b>Script Completed:</b> <code>{fname}</code> exited normally (Code 0).\n\n"
                    f"<b>Output:</b>\n<pre>{recent_err[-2000:]}</pre>"
                )
                markup = get_main_menu_keyboard()
            notify_all_admins(alert_text, reply_markup=markup)

def start_child_app(filename="bot.py"):
    global child_process, child_process_name, child_process_start_time, child_logs
    
    # Strip any prefix like scripts/
    base_filename = os.path.basename(filename)
    full_path = os.path.join(SCRIPTS_DIR, base_filename)
    
    if not os.path.exists(full_path):
        # Fallback to workspace root if not in scripts
        if os.path.exists(os.path.join(WORKSPACE_DIR, filename)):
            full_path = os.path.join(WORKSPACE_DIR, filename)
        else:
            return False, f"File <code>{base_filename}</code> scripts folder me nahi mili."
    
    if child_process and child_process.poll() is None:
        stop_child_app()
    
    try:
        child_logs = []
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        # Ensure python path includes scripts folder so all local modules import smoothly
        env["PYTHONPATH"] = f"{SCRIPTS_DIR}:{WORKSPACE_DIR}:{env.get('PYTHONPATH', '')}"
        env.update(config.get("env_vars", {}))
        
        cmd = [sys.executable, "-u", full_path]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=SCRIPTS_DIR, # All generated files (.db, .log, etc) will be saved in scripts/!
            env=env
        )
        child_process = proc
        child_process_name = base_filename
        child_process_start_time = time.time()
        
        threading.Thread(target=log_stream_reader, args=(proc.stdout,), daemon=True).start()
        threading.Thread(target=child_watchdog, args=(proc, base_filename), daemon=True).start()
        
        # Initial health check (wait 1.5s to verify process stays alive)
        time.sleep(1.5)
        
        poll_res = proc.poll()
        if poll_res is not None:
            err_msg = "\n".join(child_logs) if child_logs else "(No output recorded)"
            child_process = None
            child_process_name = None
            child_process_start_time = None
            return False, (
                f"❌ <b>{base_filename} fail ho gaya (Exit Code: {poll_res})</b>\n\n"
                f"<b>Error Details:</b>\n<pre>{err_msg[-2500:]}</pre>\n\n"
                f"💡 <i>Tip: Missing package hone par <b>Install Pip Package</b> use karein.</i>"
            )
        
        return True, f"✨ <b>{base_filename}</b> started successfully!\n🆔 Process ID: <code>{proc.pid}</code>\n🟢 State: Active (Running)"
    except Exception as e:
        return False, f"❌ Start error: {e}"

def stop_child_app():
    global child_process, child_process_name, child_process_start_time
    config["auto_run_file"] = None
    save_config(config)
    
    stopped_any = False
    name = child_process_name or "bot.py"
    
    if child_process and child_process.poll() is None:
        pid = child_process.pid
        try:
            parent = psutil.Process(pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            parent.kill()
            child_process.wait(timeout=2)
            stopped_any = True
        except Exception as e:
            logger.error(f"Error killing process: {e}")
            try:
                child_process.kill()
                stopped_any = True
            except Exception:
                pass
    
    child_process = None
    child_process_name = None
    child_process_start_time = None
    
    # Also kill any stray python bot.py processes running in workspace
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            cmd = " ".join(p.info.get('cmdline') or [])
            if "bot.py" in cmd and p.info['pid'] != os.getpid():
                p.kill()
                stopped_any = True
        except Exception:
            pass

    if stopped_any:
        return True, f"🛑 <b>{name}</b> ko successfully stop aur kill kar diya gaya."
    return False, "Abhi koi script run nahi ho rahi hai."

def restart_child_app():
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
        logger.error(f"Workflow dispatch error: {e}")
        return False

# ---------------------------------------------------------------------------
# Visual UI & Keyboards
# ---------------------------------------------------------------------------
def get_main_menu_keyboard():
    is_alive = child_process and child_process.poll() is None
    
    if is_alive and child_process_name:
        cu_sec = int(time.time() - child_process_start_time) if child_process_start_time else 0
        ch, cr = divmod(cu_sec, 3600)
        cm, _ = divmod(cr, 60)
        status_btn = f"🟢 {child_process_name} ({ch}h {cm}m)"
    else:
        status_btn = "🔴 Script: STOPPED"
    
    ram = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    stats_btn = f"⚡ CPU: {cpu}% | RAM: {ram.percent}%"
    
    return {
        "inline_keyboard": [
            [
                {"text": status_btn, "callback_data": "menu_status"},
                {"text": stats_btn, "callback_data": "menu_status"}
            ],
            [
                {"text": "🚀 Scripts Runner", "callback_data": "menu_runner"},
                {"text": "🛑 Stop Script", "callback_data": "menu_stop"}
            ],
            [
                {"text": "🔄 Restart Script", "callback_data": "menu_restart"},
                {"text": "📋 Live Logs", "callback_data": "menu_logs"}
            ],
            [
                {"text": "📂 Workspace Files", "callback_data": "menu_files"},
                {"text": "📦 Install Pip", "callback_data": "menu_pip_prompt"}
            ],
            [
                {"text": "💻 Linux Shell", "callback_data": "menu_sh_prompt"},
                {"text": "💾 Cloud Sync", "callback_data": "menu_sync"}
            ]
        ]
    }

def get_back_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "🔙 Main Menu", "callback_data": "menu_main"}]
        ]
    }

def render_dashboard_text():
    uptime_sec = int(time.time() - START_TIME)
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    
    is_alive = child_process and child_process.poll() is None
    child_status = "🟢 Active (Running)" if is_alive else "🔴 Inactive (Stopped)"
    
    child_uptime_str = "N/A"
    if is_alive and child_process_start_time:
        cu_sec = int(time.time() - child_process_start_time)
        ch, cr = divmod(cu_sec, 3600)
        cm, cs = divmod(cr, 60)
        child_uptime_str = f"{ch}h {cm}m {cs}s"

    ram = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)

    text = (
        "⚡ <b>24/7 Cloud Relay Control Panel</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🖥️ <b>Server Host:</b> GitHub Actions (Ubuntu)\n"
        f"⏱️ <b>Runner Uptime:</b> {hours}h {minutes}m {seconds}s\n"
        f"⚙️ <b>Active Script:</b> <code>{child_process_name or 'None'}</code>\n"
        f"📊 <b>Script State:</b> {child_status}\n"
        f"⏳ <b>Script Uptime:</b> {child_uptime_str}\n"
        f"💾 <b>RAM Usage:</b> {ram.percent}% ({ram.used // (1024*1024)}MB / {ram.total // (1024*1024)}MB)\n"
        f"📈 <b>CPU Load:</b> {cpu}%\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Aap koi bhi .py file ya requirements.txt direct chat me bhej sakte hain!</i>"
    )
    return text

# ---------------------------------------------------------------------------
# Message & Interactive Step Handler
# ---------------------------------------------------------------------------
def handle_text_message(chat_id, user_id, text):
    global user_states
    
    # 1. Admin Authentication Check
    if not is_admin(user_id):
        send_tg_message(chat_id, f"⛔ <b>Access Denied:</b> User ID <code>{user_id}</code> is not authorized.")
        return

    raw_text = text.strip()
    state = user_states.get(user_id)

    # Check for cancel command
    if raw_text in ["/cancel", "Cancel", "❌ Cancel"]:
        user_states.pop(user_id, None)
        send_tg_message(chat_id, "❌ Action cancelled.", reply_markup=get_main_menu_keyboard())
        return

    # Check state machine for pending interactive inputs
    if state == "WAITING_PIP_PACKAGE":
        user_states.pop(user_id, None)
        pkg_name = raw_text.replace("pip install", "").strip()
        send_tg_message(chat_id, f"⏳ <b>Installing package:</b> <code>{pkg_name}</code>...")
        res = subprocess.run([sys.executable, "-m", "pip", "install", pkg_name], capture_output=True, text=True)
        out = (res.stdout + "\n" + res.stderr).strip()
        result_msg = (
            f"📦 <b>Package: {pkg_name}</b>\n\n"
            f"<pre>{out[-2500:]}</pre>"
        )
        send_tg_message(chat_id, result_msg, reply_markup=get_main_menu_keyboard())
        return

    elif state == "WAITING_SHELL_CMD":
        user_states.pop(user_id, None)
        send_tg_message(chat_id, f"⚡ <b>Executing:</b> <code>{raw_text}</code>...")
        res = subprocess.run(raw_text, shell=True, capture_output=True, text=True, cwd=WORKSPACE_DIR)
        out = (res.stdout + "\n" + res.stderr).strip() or "(No output)"
        result_msg = (
            f"💻 <b>Command Output:</b>\n\n"
            f"<pre>{out[-2800:]}</pre>"
        )
        send_tg_message(chat_id, result_msg, reply_markup=get_main_menu_keyboard())
        return

    elif state == "WAITING_RUN_FILE":
        user_states.pop(user_id, None)
        filename = raw_text
        if not filename.endswith(".py"):
            filename += ".py"
        ok, msg = start_child_app(filename)
        send_tg_message(chat_id, msg, reply_markup=get_main_menu_keyboard())
        return

    # Default Commands
    if raw_text in ["/start", "/menu", "/help"]:
        send_tg_message(chat_id, render_dashboard_text(), reply_markup=get_main_menu_keyboard())
    
    elif raw_text == "/status":
        send_tg_message(chat_id, render_dashboard_text(), reply_markup=get_main_menu_keyboard())
    
    elif raw_text.startswith("/run"):
        parts = raw_text.split()
        if len(parts) > 1:
            ok, msg = start_child_app(parts[1])
            send_tg_message(chat_id, msg, reply_markup=get_main_menu_keyboard())
        elif raw_text in ["/runner", "/run", "/scripts"]:
            prompt_runner_menu(chat_id, user_id)
        else:
            # Show interactive run menu
            prompt_run_menu(chat_id, user_id)

    elif raw_text in ["/stop", "/stop_app", "/kill"]:
        ok, msg = stop_child_app()
        send_tg_message(chat_id, msg, reply_markup=get_main_menu_keyboard())

    elif raw_text in ["/restart"]:
        send_tg_message(chat_id, "🔄 Restarting application...")
        ok, msg = restart_child_app()
        send_tg_message(chat_id, msg, reply_markup=get_main_menu_keyboard())

    elif raw_text.startswith("/logs"):
        show_logs_view(chat_id)

    elif raw_text == "/files":
        show_files_view(chat_id)

    elif raw_text.startswith("/pip"):
        parts = raw_text.split()
        if len(parts) > 1:
            pkg = " ".join(parts[1:]).replace("install", "").strip()
            send_tg_message(chat_id, f"⏳ Installing <code>{pkg}</code>...")
            res = subprocess.run([sys.executable, "-m", "pip", "install", pkg], capture_output=True, text=True)
            send_tg_message(chat_id, f"<pre>{res.stdout[-2500:]}</pre>", reply_markup=get_main_menu_keyboard())
        else:
            prompt_pip_menu(chat_id, user_id)

    elif raw_text.startswith("/sh") or raw_text.startswith("/exec"):
        parts = raw_text.split()
        if len(parts) > 1:
            cmd = " ".join(parts[1:])
            res = subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=WORKSPACE_DIR)
            out = (res.stdout + "\n" + res.stderr).strip() or "(No output)"
            send_tg_message(chat_id, f"<pre>{out[-2800:]}</pre>", reply_markup=get_main_menu_keyboard())
        else:
            prompt_sh_menu(chat_id, user_id)

    elif raw_text.startswith("/addadmin"):
        parts = raw_text.split()
        if len(parts) > 1 and parts[1].isdigit():
            new_id = int(parts[1])
            if "admin_ids" not in config:
                config["admin_ids"] = []
            if new_id not in config["admin_ids"]:
                config["admin_ids"].append(new_id)
                save_config(config)
                git_sync_to_github(f"Add admin {new_id}")
                send_tg_message(chat_id, f"✅ Added User ID <code>{new_id}</code> as authorized admin.", reply_markup=get_main_menu_keyboard())
            else:
                send_tg_message(chat_id, f"ℹ️ User ID <code>{new_id}</code> is already an admin.", reply_markup=get_main_menu_keyboard())
        else:
            send_tg_message(chat_id, "Usage: <code>/addadmin 123456789</code>")

    elif raw_text.startswith("/admins"):
        admin_list_str = "\n".join([f"• <code>{x}</code>" for x in config.get("admin_ids", [])])
        send_tg_message(chat_id, f"👑 <b>Authorized Admins:</b>\n{admin_list_str}", reply_markup=get_main_menu_keyboard())

    elif raw_text == "/sync":
        send_tg_message(chat_id, "⏳ Syncing all files to GitHub...")
        ok, msg = git_sync_to_github()
        send_tg_message(chat_id, f"{'✅' if ok else '❌'} {msg}", reply_markup=get_main_menu_keyboard())

    else:
        # If user just types text, show dashboard
        send_tg_message(chat_id, render_dashboard_text(), reply_markup=get_main_menu_keyboard())

# ---------------------------------------------------------------------------
# Interactive Submenus
# ---------------------------------------------------------------------------
def prompt_runner_menu(chat_id, user_id, message_id=None):
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    files = sorted([f for f in os.listdir(SCRIPTS_DIR) if f.endswith(".py")])
    
    is_alive = child_process and child_process.poll() is None
    
    buttons = []
    if not files:
        text = (
            "🚀 <b>Scripts Runner Manager</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📁 <code>scripts/</code> folder me abhi koi Python file nahi mili.\n\n"
            "💡 <i>Aap chat me direct koi bhi <code>.py</code> file bhej sakte hain, wo automatically <code>scripts/</code> folder me add ho jayegi!</i>"
        )
    else:
        text = (
            "🚀 <b>Scripts Runner Manager</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Active Process:</b> {'🟢 ' + child_process_name if is_alive else '🔴 None (Stopped)'}\n"
            f"• <b>Total Scripts in GitHub:</b> {len(files)}\n\n"
            "<i>Neeche kisi bhi script par click karke use run karein:</i>"
        )
        for py in files:
            is_this_running = is_alive and child_process_name == py
            btn_prefix = "🟢 [RUNNING] " if is_this_running else "▶️ Run "
            buttons.append([{"text": f"{btn_prefix}{py}", "callback_data": f"exec_run_{py}"}])

    buttons.append([{"text": "📤 Upload New Script", "callback_data": "menu_upload_prompt"}])
    if is_alive:
        buttons.append([{"text": "🛑 Stop Current Script", "callback_data": "menu_stop"}])
    buttons.append([{"text": "🔙 Main Menu", "callback_data": "menu_main"}])

    markup = {"inline_keyboard": buttons}
    if message_id:
        edit_tg_message(chat_id, message_id, text, reply_markup=markup)
    else:
        send_tg_message(chat_id, text, reply_markup=markup)

def prompt_run_menu(chat_id, user_id, message_id=None):
    prompt_runner_menu(chat_id, user_id, message_id)

def prompt_pip_menu(chat_id, user_id, message_id=None):
    user_states[user_id] = "WAITING_PIP_PACKAGE"
    text = (
        "📦 <b>Install Python Package</b>\n\n"
        "Please send the package name you want to install:\n"
        "<i>(Example: <code>telethon</code>, <code>aiohttp</code>, <code>bs4</code>)</i>"
    )
    markup = {
        "inline_keyboard": [
            [{"text": "❌ Cancel", "callback_data": "menu_main"}]
        ]
    }
    if message_id:
        edit_tg_message(chat_id, message_id, text, reply_markup=markup)
    else:
        send_tg_message(chat_id, text, reply_markup=markup)

def prompt_sh_menu(chat_id, user_id, message_id=None):
    user_states[user_id] = "WAITING_SHELL_CMD"
    text = (
        "💻 <b>Linux Shell Terminal</b>\n\n"
        "Please send the bash command you want to execute:\n"
        "<i>(Example: <code>ls -la</code>, <code>df -h</code>, <code>python --version</code>)</i>"
    )
    markup = {
        "inline_keyboard": [
            [{"text": "❌ Cancel", "callback_data": "menu_main"}]
        ]
    }
    if message_id:
        edit_tg_message(chat_id, message_id, text, reply_markup=markup)
    else:
        send_tg_message(chat_id, text, reply_markup=markup)

def show_logs_view(chat_id, message_id=None):
    is_alive = child_process and child_process.poll() is None
    status_header = f"🟢 <code>{child_process_name}</code> (Running)" if is_alive else f"🔴 <code>{child_process_name or 'None'}</code> (Stopped)"
    
    if not child_logs:
        log_text = "<i>No logs recorded yet. Start a script to see live output.</i>"
    else:
        recent = "\n".join(child_logs[-30:])
        escaped_recent = html.escape(recent)
        log_text = f"<pre>{escaped_recent}</pre>"
    
    text = (
        "📋 <b>Live Execution Logs</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Status:</b> {status_header}\n"
        f"• <b>Total Logs in Buffer:</b> {len(child_logs)} lines\n\n"
        f"{log_text}"
    )
    markup = {
        "inline_keyboard": [
            [{"text": "🔄 Refresh Logs", "callback_data": "menu_logs"}],
            [{"text": "🛑 Stop Script", "callback_data": "menu_stop"}, {"text": "🔄 Restart", "callback_data": "menu_restart"}],
            [{"text": "🔙 Main Menu", "callback_data": "menu_main"}]
        ]
    }
    if message_id:
        edit_tg_message(chat_id, message_id, text, reply_markup=markup)
    else:
        send_tg_message(chat_id, text, reply_markup=markup)

def show_files_view(chat_id, message_id=None):
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    files = sorted([f for f in os.listdir(SCRIPTS_DIR) if not f.startswith(".") and f != "__pycache__"])
    is_alive = child_process and child_process.poll() is None
    
    file_lines = []
    download_buttons = []
    
    if not files:
        file_lines.append("<i>Scripts folder (scripts/) is currently empty.\nSend any .py script or requirements.txt to upload!</i>")
    else:
        for it in files:
            p = os.path.join(SCRIPTS_DIR, it)
            if os.path.isfile(p):
                sz = os.path.getsize(p)
                is_this_running = is_alive and child_process_name == it
                status_icon = "🟢" if is_this_running else "📄"
                file_lines.append(f"• {status_icon} <code>{it}</code> ({sz} bytes){' <b>[RUNNING]</b>' if is_this_running else ''}")
                
                if it.endswith(".py"):
                    if is_this_running:
                        run_btn = {"text": "🛑 Stop", "callback_data": "menu_stop"}
                    else:
                        run_btn = {"text": "▶️ Run", "callback_data": f"exec_run_{it}"}
                    
                    download_buttons.append([
                        {"text": f"📥 {it}", "callback_data": f"file_dl_{it}"},
                        run_btn,
                        {"text": "🗑️ Delete", "callback_data": f"file_del_{it}"}
                    ])
                else:
                    download_buttons.append([
                        {"text": f"📥 {it}", "callback_data": f"file_dl_{it}"},
                        {"text": "🗑️ Delete", "callback_data": f"file_del_{it}"}
                    ])
    
    text = (
        "📂 <b>Scripts File Manager</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 <b>Target Directory:</b> <code>scripts/</code> (GitHub Repository)\n"
        f"📊 <b>Total Files:</b> {len(files)}\n\n"
        + "\n".join(file_lines)
        + "\n\n<i>Tap a button below to Download, Run, or Delete:</i>"
    )
    download_buttons.append([{"text": "🚀 Scripts Runner", "callback_data": "menu_runner"}])
    download_buttons.append([{"text": "📤 Upload New Script", "callback_data": "menu_upload_prompt"}])
    download_buttons.append([{"text": "🔙 Main Menu", "callback_data": "menu_main"}])
    markup = {"inline_keyboard": download_buttons}
    
    if message_id:
        edit_tg_message(chat_id, message_id, text, reply_markup=markup)
    else:
        send_tg_message(chat_id, text, reply_markup=markup)

def prompt_stop_menu(chat_id, user_id, message_id=None):
    is_alive = child_process and child_process.poll() is None
    if not is_alive or not child_process_name:
        text = "ℹ️ <b>No Running Scripts</b>\n\nAbhi koi bhi script active/run nahi ho rahi hai."
        markup = {"inline_keyboard": [[{"text": "🔙 Main Menu", "callback_data": "menu_main"}]]}
    else:
        cu_sec = int(time.time() - child_process_start_time) if child_process_start_time else 0
        ch, cr = divmod(cu_sec, 3600)
        cm, cs = divmod(cr, 60)
        
        text = (
            "🛑 <b>Running Scripts Manager</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Active Script:</b> <code>{child_process_name}</code>\n"
            f"• <b>PID:</b> <code>{child_process.pid}</code>\n"
            f"• <b>Uptime:</b> {ch}h {cm}m {cs}s\n\n"
            "<i>Neeche script par click karein aur stop confirm karein:</i>"
        )
        markup = {
            "inline_keyboard": [
                [{"text": f"🛑 Stop {child_process_name}", "callback_data": f"confirm_stop_prompt_{child_process_name}"}],
                [{"text": "🔙 Main Menu", "callback_data": "menu_main"}]
            ]
        }
    if message_id:
        edit_tg_message(chat_id, message_id, text, reply_markup=markup)
    else:
        send_tg_message(chat_id, text, reply_markup=markup)

# ---------------------------------------------------------------------------
# Callback Query Handler (Button Clicks)
# ---------------------------------------------------------------------------
def handle_callback_query(callback_id, chat_id, user_id, message_id, data):
    if not is_admin(user_id):
        answer_callback(callback_id, "⛔ Access Denied!", show_alert=True)
        return

    # 1. Main Menu
    if data == "menu_main":
        user_states.pop(user_id, None)
        answer_callback(callback_id)
        edit_tg_message(chat_id, message_id, render_dashboard_text(), reply_markup=get_main_menu_keyboard())

    # 2. Status
    elif data == "menu_status":
        answer_callback(callback_id, "📊 Status Refreshed!")
        edit_tg_message(chat_id, message_id, render_dashboard_text(), reply_markup=get_main_menu_keyboard())

    # 3. Runner Menu
    elif data in ["menu_runner", "menu_run_select"]:
        answer_callback(callback_id)
        prompt_runner_menu(chat_id, user_id, message_id)

    # 4. Execute a specific file
    elif data.startswith("exec_run_"):
        fname = data.replace("exec_run_", "")
        answer_callback(callback_id, f"Starting {fname}...")
        ok, msg = start_child_app(fname)
        send_tg_message(chat_id, msg, reply_markup=get_main_menu_keyboard())

    # 5. Stop Script Menu
    elif data == "menu_stop":
        answer_callback(callback_id)
        prompt_stop_menu(chat_id, user_id, message_id)

    # 5b. Confirm Stop Prompt
    elif data.startswith("confirm_stop_prompt_"):
        fname = data.replace("confirm_stop_prompt_", "")
        answer_callback(callback_id)
        text = (
            f"⚠️ <b>Confirmation Required</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Kya aap sach me <code>{fname}</code> script ko <b>STOP / TERMINATE</b> karna chahte hain?"
        )
        markup = {
            "inline_keyboard": [
                [{"text": f"🛑 Yes, Stop {fname}", "callback_data": f"do_stop_{fname}"}],
                [{"text": "❌ Cancel (Keep Running)", "callback_data": "menu_main"}]
            ]
        }
        edit_tg_message(chat_id, message_id, text, reply_markup=markup)

    # 5c. Do Stop Execution
    elif data.startswith("do_stop_"):
        fname = data.replace("do_stop_", "")
        ok, msg = stop_child_app()
        answer_callback(callback_id, f"{fname} stopped!", show_alert=True)
        edit_tg_message(chat_id, message_id, f"🛑 <b>{fname} ko successfully stop kar diya gaya!</b>", reply_markup=get_main_menu_keyboard())

    # 6. Restart Script
    elif data == "menu_restart":
        answer_callback(callback_id, "Restarting...")
        ok, msg = restart_child_app()
        send_tg_message(chat_id, msg, reply_markup=get_main_menu_keyboard())

    # 7. Logs
    elif data == "menu_logs":
        answer_callback(callback_id)
        show_logs_view(chat_id, message_id)

    # 8. Files
    elif data == "menu_files":
        answer_callback(callback_id)
        show_files_view(chat_id, message_id)

    # 9. Download File
    elif data.startswith("file_dl_"):
        fname = data.replace("file_dl_", "")
        if os.path.exists(os.path.join(SCRIPTS_DIR, fname)):
            fpath = os.path.join(SCRIPTS_DIR, fname)
        else:
            fpath = os.path.join(WORKSPACE_DIR, fname)

        if os.path.exists(fpath):
            answer_callback(callback_id, f"Sending {fname}...")
            send_tg_document(chat_id, fpath, caption=f"📄 <b>{fname}</b>")
        else:
            answer_callback(callback_id, "File not found!", show_alert=True)

    # 10. Delete File Prompt (Confirmation)
    elif data.startswith("file_del_"):
        fname = data.replace("file_del_", "")
        answer_callback(callback_id)
        text = (
            f"⚠️ <b>Confirm File Deletion</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Kya aap sach me <code>scripts/{fname}</code> ko <b>delete</b> karna chahte hain?\n\n"
            "<i>Yeh file GitHub repo ke scripts/ folder se permanent delete ho jayegi.</i>"
        )
        markup = {
            "inline_keyboard": [
                [{"text": f"🗑️ Yes, Delete {fname}", "callback_data": f"do_delete_file_{fname}"}],
                [{"text": "❌ Cancel", "callback_data": "menu_files"}]
            ]
        }
        edit_tg_message(chat_id, message_id, text, reply_markup=markup)

    # 10b. Do Delete File Execution
    elif data.startswith("do_delete_file_"):
        fname = data.replace("do_delete_file_", "")
        if os.path.exists(os.path.join(SCRIPTS_DIR, fname)):
            fpath = os.path.join(SCRIPTS_DIR, fname)
        else:
            fpath = os.path.join(WORKSPACE_DIR, fname)

        if os.path.exists(fpath):
            os.remove(fpath)
            git_sync_to_github(f"Deleted scripts/{fname} via Telegram")
            answer_callback(callback_id, f"{fname} deleted!", show_alert=True)
            show_files_view(chat_id, message_id)
        else:
            answer_callback(callback_id, "File not found!", show_alert=True)

    # 11. Pip prompt
    elif data == "menu_pip_prompt":
        answer_callback(callback_id)
        prompt_pip_menu(chat_id, user_id, message_id)

    # 12. Shell prompt
    elif data == "menu_sh_prompt":
        answer_callback(callback_id)
        prompt_sh_menu(chat_id, user_id, message_id)

    # 13. Sync
    elif data == "menu_sync":
        answer_callback(callback_id, "Syncing to GitHub...")
        ok, msg = git_sync_to_github()
        send_tg_message(chat_id, f"{'✅' if ok else '❌'} {msg}", reply_markup=get_main_menu_keyboard())

    # 14. Upload Prompt
    elif data == "menu_upload_prompt":
        user_states[user_id] = "WAITING_RUN_FILE"
        answer_callback(callback_id)
        text = (
            "📤 <b>Upload New Python Script</b>\n\n"
            "Abhi chat me apni <code>.py</code> file bhej dein.\n"
            "Bot usko automatically GitHub ke <code>scripts/</code> folder me save karega!"
        )
        edit_tg_message(chat_id, message_id, text, reply_markup=get_back_keyboard())

# ---------------------------------------------------------------------------
# Document & File Upload Handler
# ---------------------------------------------------------------------------
def handle_document_upload(chat_id, user_id, doc):
    global user_states
    if not is_admin(user_id):
        send_tg_message(chat_id, "⛔ Access Denied.")
        return
    
    file_id = doc.get("file_id")
    file_name = doc.get("file_name", f"file_{int(time.time())}")
    
    # Save script assets directly into scripts/ folder
    os.makedirs(SCRIPTS_DIR, exist_ok=True)
    scripts_path = os.path.join(SCRIPTS_DIR, file_name)
    root_path = os.path.join(WORKSPACE_DIR, file_name)
    
    send_tg_message(chat_id, f"📥 <b>Receiving {file_name}...</b>")
    ok, err = download_tg_file(file_id, scripts_path)
    
    if not ok:
        send_tg_message(chat_id, f"❌ Download failed: {err}")
        return

    # If requirements.txt, also sync to root workspace
    if file_name == "requirements.txt":
        import shutil
        try:
            shutil.copy(scripts_path, root_path)
        except Exception:
            pass
    
    # Auto-commit to GitHub scripts/ folder
    git_sync_to_github(f"Upload {file_name} into scripts/ folder")
    
    current_state = user_states.get(user_id)
    
    # 1. If uploaded requirements.txt
    if file_name == "requirements.txt":
        send_tg_message(chat_id, "📦 <b>scripts/requirements.txt received!</b>\n⏳ Installing dependencies in real-time...")
        res = subprocess.run([sys.executable, "-m", "pip", "install", "-r", scripts_path], capture_output=True, text=True)
        
        # Check if user had previously sent a Python file waiting for this requirements.txt
        if isinstance(current_state, dict) and current_state.get("action") == "WAITING_REQ_FOR_PY":
            target_py = current_state.get("target_py", "bot.py")
            user_states.pop(user_id, None)
            send_tg_message(chat_id, f"✅ <b>Dependencies installed!</b>\n🚀 Now auto-launching <code>{target_py}</code>...")
            ok_run, run_msg = start_child_app(target_py)
            send_tg_message(chat_id, run_msg, reply_markup=get_main_menu_keyboard())
        else:
            out_summary = res.stdout[-2000:] if res.stdout else "All requirements satisfied."
            send_tg_message(chat_id, f"✅ <b>Dependencies Installed:</b>\n<pre>{out_summary}</pre>")
            prompt_runner_menu(chat_id, user_id)
    
    # 2. If uploaded a .py script
    elif file_name.endswith(".py"):
        user_states[user_id] = {
            "action": "WAITING_REQ_FOR_PY",
            "target_py": file_name
        }
        
        text = (
            f"✨ <b>Python Script Saved:</b> <code>scripts/{file_name}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "📁 File GitHub repository ke <b>scripts/</b> folder me save ho chuki hai!\n\n"
            "📦 <b>requirements.txt bhej sakte hain</b> taaki dependencies install ho sakein,\n"
            "ya neeche button se <b>Direct Run</b> ya <b>Scripts Runner</b> open kar sakte hain:"
        )
        markup = {
            "inline_keyboard": [
                [{"text": f"▶️ Run {file_name} Now", "callback_data": f"exec_run_{file_name}"}],
                [{"text": "🚀 Open Scripts Runner", "callback_data": "menu_runner"}],
                [{"text": "🔙 Main Menu", "callback_data": "menu_main"}]
            ]
        }
        send_tg_message(chat_id, text, reply_markup=markup)
        
    else:
        send_tg_message(
            chat_id,
            f"✅ <b>{file_name}</b> GitHub repository ke <code>scripts/</code> folder me save ho gayi hai.",
            reply_markup=get_main_menu_keyboard()
        )

# ---------------------------------------------------------------------------
# Polling Engine
# ---------------------------------------------------------------------------
def telegram_polling_loop():
    logger.info("🤖 Telegram Polling Engine active...")
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
                        
                        # Handle Callback Queries (Button Clicks)
                        if "callback_query" in update:
                            cq = update["callback_query"]
                            cb_id = cq["id"]
                            c_user = cq["from"]["id"]
                            c_chat = cq["message"]["chat"]["id"]
                            c_msg_id = cq["message"]["message_id"]
                            c_data = cq.get("data", "")
                            handle_callback_query(cb_id, c_chat, c_user, c_msg_id, c_data)
                        
                        # Handle Normal Messages
                        elif "message" in update:
                            msg = update["message"]
                            chat_id = msg.get("chat", {}).get("id")
                            user_id = msg.get("from", {}).get("id")
                            
                            if not chat_id or not user_id:
                                continue
                            
                            if "text" in msg:
                                handle_text_message(chat_id, user_id, msg["text"])
                            elif "document" in msg:
                                handle_document_upload(chat_id, user_id, msg["document"])
            time.sleep(0.5)
        except Exception as e:
            logger.error(f"Telegram polling error: {e}")
            time.sleep(3)

# ---------------------------------------------------------------------------
# Main Orchestrator & Auto-Runner
# ---------------------------------------------------------------------------
def main():
    global IS_RUNNING
    logger.info("=" * 60)
    logger.info(f"🚀 Telegram Relay Controller Initialized [Run #{RUN_ID}]")
    logger.info("=" * 60)
    
    # NOTE: User scripts DO NOT auto-start on boot. Only Telegram controller runs and waits for Admin to launch scripts via Runner menu.

    # Start Telegram polling thread
    tg_thread = threading.Thread(target=telegram_polling_loop, daemon=True, name="TGPolling")
    tg_thread.start()
    
    # Watchdog loop for 5.5 hours duration
    while IS_RUNNING:
        elapsed = time.time() - START_TIME
        if elapsed >= RUN_DURATION_SECONDS:
            logger.info(f"⏳ 5.5 Hours reached. Triggering Handoff...")
            break
        time.sleep(5)
    
    # --- HANDOFF SEQUENCE ---
    IS_RUNNING = False # Stop Telegram polling immediately on old runner
    
    notify_all_admins(
        "🔄 <b>5.5 Hours Relay Limit Reached:</b>\n"
        "Backing up workspace and transitioning to next runner..."
    )
    
    stop_child_app()
    git_sync_to_github("Auto-backup before Relay Handoff")
    trigger_next_runner()
    time.sleep(5)
    logger.info("Handoff sequence complete. Exiting.")

if __name__ == "__main__":
    main()
