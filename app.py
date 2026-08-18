import os
import sys
import time
import json
import random
import logging
import signal
import threading
import uuid
from datetime import datetime
import requests

# ---------------------------------------------------------------------------
# Logging Configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(threadName)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("RelayRunner")

# ---------------------------------------------------------------------------
# Environment Variables & Configuration
# ---------------------------------------------------------------------------
GH_PAT = os.environ.get("GH_PAT", "")
GIST_ID = os.environ.get("GIST_ID", "")
RUN_ID = os.environ.get("GITHUB_RUN_ID", str(uuid.uuid4())[:8])
REPO = os.environ.get("GITHUB_REPOSITORY", "Saini920/testgitonly")
WORKFLOW_FILE = os.environ.get("WORKFLOW_FILE", "server.yml")
WORKFLOW_REF = os.environ.get("WORKFLOW_REF", "main")

# Default run duration: 5.5 hours (330 minutes = 19800 seconds)
RUN_DURATION_SECONDS = int(os.environ.get("RUN_DURATION_SECONDS", "19800"))
LOCK_TTL_SECONDS = int(os.environ.get("LOCK_TTL_SECONDS", "180"))
HEARTBEAT_INTERVAL = int(os.environ.get("HEARTBEAT_INTERVAL", "30"))
CHECKPOINT_INTERVAL = int(os.environ.get("CHECKPOINT_INTERVAL", "60"))

START_TIME = time.time()
IS_RUNNING = True

app_state = {
    "tasks_executed": 0,
    "last_checkpoint": 0,
    "run_id": str(RUN_ID),
    "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
}

# ---------------------------------------------------------------------------
# State Management (Gist or Memory/Local)
# ---------------------------------------------------------------------------
def get_gist_headers():
    return {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }

def fetch_remote_state():
    if not GH_PAT or not GIST_ID:
        return {"schema_version": 1, "lock_owner": str(RUN_ID), "lock_expiry": int(time.time()) + 9999, "data": app_state}
    
    url = f"https://api.github.com/gists/{GIST_ID}"
    try:
        resp = requests.get(url, headers=get_gist_headers(), timeout=10)
        if resp.status_code == 200:
            files = resp.json().get("files", {})
            if "state.json" in files:
                return json.loads(files["state.json"].get("content", "{}"))
    except Exception as e:
        logger.error(f"Gist fetch error: {e}")
    return None

def update_remote_state(state_dict):
    if not GH_PAT or not GIST_ID:
        return True
    url = f"https://api.github.com/gists/{GIST_ID}"
    payload = {"files": {"state.json": {"content": json.dumps(state_dict, indent=2)}}}
    try:
        resp = requests.patch(url, headers=get_gist_headers(), json=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        logger.error(f"Gist update error: {e}")
        return False

# ---------------------------------------------------------------------------
# Self-Trigger: Next Runner Launch via GitHub REST API
# ---------------------------------------------------------------------------
def trigger_next_runner():
    """Dispatches the next GitHub Actions workflow run before timeout."""
    token = GH_PAT or os.environ.get("GITHUB_TOKEN", "")
    if not token or not REPO:
        logger.warning("No token or repository set for self-trigger.")
        return False
    
    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    payload = {"ref": WORKFLOW_REF}
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    }
    
    logger.info(f"🚀 Triggering next workflow: {url} on branch '{WORKFLOW_REF}'...")
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 204:
            logger.info("✅ Next workflow dispatched successfully!")
            return True
        else:
            logger.error(f"Failed to dispatch workflow (status {resp.status_code}): {resp.text}")
    except Exception as e:
        logger.error(f"Error dispatching workflow: {e}")
    return False

# ---------------------------------------------------------------------------
# Continuous Background Worker (Your Actual Bot/Tasks)
# ---------------------------------------------------------------------------
def background_worker():
    """Continuous 24/7 background task loop."""
    logger.info("⚡ Background task worker active.")
    while IS_RUNNING:
        try:
            app_state["tasks_executed"] += 1
            uptime_sec = int(time.time() - START_TIME)
            hours, remainder = divmod(uptime_sec, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            logger.info(f"🟢 [Task Cycle #{app_state['tasks_executed']}] Runner Uptime: {hours}h {minutes}m {seconds}s | Status: Normal")
            
            # Put your actual Python code (Telegram bot, scraper, etc.) here
            time.sleep(30)
            
        except Exception as e:
            logger.error(f"Error in task cycle: {e}")
            time.sleep(5)

# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------
def main():
    logger.info("=" * 60)
    logger.info(f"🚀 GitHub Actions Relay Runner Started [Run ID: {RUN_ID}]")
    logger.info(f"Target Duration: {RUN_DURATION_SECONDS}s ({RUN_DURATION_SECONDS/60:.1f} minutes)")
    logger.info("=" * 60)
    
    # Start background task thread
    worker_thread = threading.Thread(target=background_worker, daemon=True, name="WorkerThread")
    worker_thread.start()
    
    # Main execution timer loop
    while IS_RUNNING:
        elapsed = time.time() - START_TIME
        if elapsed >= RUN_DURATION_SECONDS:
            logger.info(f"⏳ Time limit reached ({RUN_DURATION_SECONDS}s). Preparing graceful handoff...")
            break
        time.sleep(5)
    
    # Handoff to next runner
    logger.info("🔄 Initiating successor runner handoff...")
    trigger_next_runner()
    
    logger.info("Holding safety buffer (15s) for next runner queue...")
    time.sleep(15)
    logger.info("🎉 Runner finished successfully. Exiting.")

if __name__ == "__main__":
    main()
