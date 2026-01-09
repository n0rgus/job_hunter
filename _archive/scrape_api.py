# api/scrape_api.py
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import time, os, io
from services.scrape_runner import start_scrape, status, LOG_FILE

router = APIRouter(prefix="/api/scrape", tags=["scrape"])

@router.post("/start")
async def start_endpoint(site_id: str | None = None):
    """Optionally accept site_id (passed through if your script supports args)."""
    args = []
    if site_id:
        # Adjust to match your CLI if applicable, e.g. ["--site-id", site_id]
        args += ["--site", site_id]

    res = start_scrape(args)
    if not res.get("started"):
        if res.get("reason") == "already_running":
            raise HTTPException(status_code=409, detail="Scraper already running")
        raise HTTPException(status_code=500, detail="Could not start scraper")
    return JSONResponse(res)


def _sse_format(line: str) -> bytes:
    return f"data: {line.rstrip()}\n\n".encode("utf-8", errors="ignore")

@router.get("/log/stream")
def stream_logs():
    """Server-Sent Events stream of job_hunter.log"""
    def gen():
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        path = LOG_FILE
        # Tail-from-end
        with open(path, "rb", buffering=0) as f:
            try:
                f.seek(max(0, os.path.getsize(path) - 8192))
            except Exception:
                pass
            # Send a small banner
            yield _sse_format("📜 connected to log stream")
            buf = b""
            while True:
                chunk = f.read(1024)
                if not chunk:
                    time.sleep(0.5)
                else:
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        try:
                            yield _sse_format(line.decode("utf-8", errors="ignore"))
                        except Exception:
                            pass
    return StreamingResponse(gen(), media_type="text/event-stream")

@router.get("/status")
async def status_endpoint():
    return JSONResponse(status())