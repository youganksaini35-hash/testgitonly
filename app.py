import os
import sys
import time
import psutil
import logging
import threading
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
import uvicorn

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("HFRunner")

recent_logs = []

def log_event(message: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{timestamp}] [{level}] {message}"
    recent_logs.append(entry)
    if len(recent_logs) > 50:
        recent_logs.pop(0)
    logger.info(message)

# ---------------------------------------------------------------------------
# Application State & Configuration
# ---------------------------------------------------------------------------
START_TIME = time.time()
PORT = int(os.environ.get("PORT", "7860"))
IS_RUNNING = True

state = {
    "tasks_executed": 0,
    "last_run_time": None,
    "status": "Running 24/7",
    "custom_data": {}
}

# ---------------------------------------------------------------------------
# Background 24/7 Worker (Put Your Continuous Bot / Scraper / Tasks Here)
# ---------------------------------------------------------------------------
def continuous_background_worker():
    """
    Yeh background thread 24/7 continuous chalti rahegi bina kisi timeout ke.
    Aap yahan apna Telegram Bot, Scraper, ya koi bhi Python code run kar sakte hain.
    """
    log_event("🚀 Background 24/7 Worker started successfully!")
    
    while IS_RUNNING:
        try:
            # ---------------------------------------------------------------
            # 💡 TUMHARA CUSTOM CODE YAHAN AAYEGA:
            # e.g., Telegram polling, API scraping, Database sync, etc.
            # ---------------------------------------------------------------
            state["tasks_executed"] += 1
            state["last_run_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            log_event(f"Task cycle #{state['tasks_executed']} executed successfully.")
            
            # Har 30 second me ek task cycle run hoga (apne hisaab se change karein)
            time.sleep(30)
            
        except Exception as e:
            log_event(f"Error in background task: {e}", level="ERROR")
            time.sleep(5)

# ---------------------------------------------------------------------------
# Web Dashboard & API (FastAPI)
# ---------------------------------------------------------------------------
app = FastAPI(title="24/7 Python Runner on Hugging Face")

@app.get("/", response_class=HTMLResponse)
async def web_dashboard():
    uptime_sec = int(time.time() - START_TIME)
    hours, remainder = divmod(uptime_sec, 3600)
    minutes, seconds = divmod(remainder, 60)
    uptime_str = f"{hours}h {minutes}m {seconds}s"
    
    ram = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    
    logs_html = "".join([f"<div class='log-line'>{line}</div>" for line in reversed(recent_logs[-15:])])
    
    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>24/7 Python Runner Dashboard</title>
        <style>
            * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
            body {{ background-color: #0f172a; color: #f8fafc; padding: 24px; min-height: 100vh; }}
            .container {{ max-width: 900px; margin: 0 auto; }}
            .header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; border-bottom: 1px solid #1e293b; padding-bottom: 16px; }}
            .badge {{ display: inline-flex; align-items: center; gap: 6px; padding: 6px 12px; background: rgba(34, 197, 94, 0.15); color: #4ade80; border-radius: 9999px; font-weight: 600; font-size: 14px; border: 1px solid rgba(34, 197, 94, 0.3); }}
            .badge-dot {{ width: 8px; height: 8px; background: #22c55e; border-radius: 50%; animation: pulse 2s infinite; }}
            @keyframes pulse {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.4; }} 100% {{ opacity: 1; }} }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }}
            .card {{ background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }}
            .card-title {{ font-size: 13px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }}
            .card-val {{ font-size: 26px; font-weight: 700; color: #f1f5f9; }}
            .logs-container {{ background: #0b1120; border-radius: 12px; padding: 20px; border: 1px solid #1e293b; font-family: monospace; font-size: 13px; max-height: 320px; overflow-y: auto; }}
            .log-line {{ padding: 4px 0; color: #cbd5e1; border-bottom: 1px solid #1e293b; }}
            .log-line:first-child {{ color: #38bdf8; font-weight: 600; }}
            .footer {{ margin-top: 24px; text-align: center; color: #64748b; font-size: 13px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <div>
                    <h1 style="font-size: 22px;">⚡ Hugging Face 24/7 Background Runner</h1>
                    <p style="color: #94a3b8; font-size: 14px; margin-top: 4px;">Always-on Python execution with free HTTPS endpoint</p>
                </div>
                <div class="badge">
                    <span class="badge-dot"></span> ALWAYS ON
                </div>
            </div>

            <div class="grid">
                <div class="card">
                    <div class="card-title">Server Uptime</div>
                    <div class="card-val">{uptime_str}</div>
                </div>
                <div class="card">
                    <div class="card-title">Tasks Cycles</div>
                    <div class="card-val">{state['tasks_executed']}</div>
                </div>
                <div class="card">
                    <div class="card-title">RAM Usage (16GB)</div>
                    <div class="card-val">{ram.percent}%</div>
                </div>
                <div class="card">
                    <div class="card-title">CPU Load</div>
                    <div class="card-val">{cpu}%</div>
                </div>
            </div>

            <h2 style="font-size: 16px; margin-bottom: 12px; color: #cbd5e1;">Live Execution Logs</h2>
            <div class="logs-container">
                {logs_html}
            </div>

            <div class="footer">
                Powered by Hugging Face Spaces • Python 3.10 • Port {PORT}
            </div>
        </div>
    </body>
    </html>
    """
    return html

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "uptime_seconds": int(time.time() - START_TIME),
        "tasks_executed": state["tasks_executed"]
    }

@app.get("/api/state")
async def get_state():
    return JSONResponse(content=state)

# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Start background worker thread
    worker_thread = threading.Thread(target=continuous_background_worker, daemon=True, name="BackgroundWorker")
    worker_thread.start()
    
    # Start Web Server on 0.0.0.0:7860
    logger.info(f"Starting server on port {PORT}...")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
