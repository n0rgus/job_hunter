# services/scrape_runner.py
import os, sys, signal, subprocess, time, json, pathlib
from datetime import datetime, timedelta

ROOT = pathlib.Path(__file__).resolve().parents[1]  # repo root
LOG_FILE = pathlib.Path(os.getenv("JOB_HUNTER_LOG", ROOT / "logs" / "job_hunter.log"))
SCRIPT = pathlib.Path(os.getenv("JOB_HUNTER_SCRIPT", ROOT / "main_scraper.py"))
RUNTIME_DIR = pathlib.Path(os.getenv("JOB_HUNTER_RUNTIME", ROOT / ".runtime"))
PID_FILE = RUNTIME_DIR / "scrape.pid"
STALE_AFTER = timedelta(hours=12)

RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

def _pidfile_is_stale() -> bool:
    if not PID_FILE.exists():
        return False
    mtime = datetime.fromtimestamp(PID_FILE.stat().st_mtime)
    return datetime.now() - mtime > STALE_AFTER

def is_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        return False

    # Best‑effort cross‑platform check without extra deps
    try:
        if os.name == "nt":
            # On Windows, fall back to tasklist check
            out = subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}"], text=True)
            return str(pid) in out
        else:
            os.kill(pid, 0)  # raises if dead
            return True
    except Exception:
        return False

def start_scrape(args: list[str] | None = None) -> dict:
    """Spawn the scraper as a detached process. Returns job metadata.
    Prevents concurrent runs via a simple PID lock.
    """
    if is_running() and not _pidfile_is_stale():
        return {"started": False, "reason": "already_running"}

    # Clean up stale lock if needed
    if _pidfile_is_stale():
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    cmd = [sys.executable, "-u", str(SCRIPT)]
    if args:
        cmd += args

    env = os.environ.copy()
    # Example: make headless configurable from env/UI if your scraper supports it
    # env["JOB_HUNTER_HEADLESS"] = env.get("JOB_HUNTER_HEADLESS", "1")

    # Ensure log file exists so the UI can begin tailing immediately
    LOG_FILE.touch(exist_ok=True)

    # Start detached so API request returns immediately
    creationflags = 0
    kwargs = {}
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        kwargs["creationflags"] = creationflags
        stdout = subprocess.DEVNULL
        stderr = subprocess.DEVNULL
    else:
        stdout = subprocess.DEVNULL
        stderr = subprocess.DEVNULL
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        cmd,
        cwd=str(ROOT),
        env=env,
        stdout=stdout,
        stderr=stderr,
        **kwargs,
    )

    PID_FILE.write_text(str(proc.pid))

    meta = {
        "started": True,
        "pid": proc.pid,
        "cmd": cmd,
        "started_at": datetime.utcnow().isoformat() + "Z",
        "log_file": str(LOG_FILE),
    }
    (RUNTIME_DIR / "last_job.json").write_text(json.dumps(meta, indent=2))
    return meta

def stop_scrape(force: bool = False) -> dict:
    if not PID_FILE.exists():
        return {"stopped": False, "reason": "not_running"}

    try:
        pid = int(PID_FILE.read_text().strip())
    except Exception:
        PID_FILE.unlink(missing_ok=True)
        return {"stopped": False, "reason": "bad_pidfile"}

    def _alive(p):
        if os.name == "nt":
            try:
                out = subprocess.check_output(["tasklist", "/FI", f"PID eq {p}"], text=True)
                return str(p) in out
            except Exception:
                return False
        else:
            try:
                os.kill(p, 0)
                return True
            except Exception:
                return False

    if os.name == "nt":
        # try graceful first (no real SIGTERM on Windows), then force
        subprocess.run(["taskkill", "/PID", str(pid), "/T"] + (["/F"] if force else []), capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        # escalate if requested
        if force:
            time.sleep(1.0)
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass

    # wait up to ~5s for exit
    for _ in range(10):
        if not _alive(pid):
            PID_FILE.unlink(missing_ok=True)
            return {"stopped": True, "pid": pid}
        time.sleep(0.5)

    # still alive
    return {"stopped": False, "reason": "still_running", "pid": pid}

def status() -> dict:
    running = is_running()
    last_log_ts = None
    if LOG_FILE.exists():
        last_log_ts = datetime.fromtimestamp(LOG_FILE.stat().st_mtime).isoformat()
    return {
        "running": running,
        "pid": int(PID_FILE.read_text()) if running else None,
        "log_file": str(LOG_FILE),
        "last_log_write": last_log_ts,
    }