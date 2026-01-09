# api/scrape_api_flask.py
from flask import Blueprint, Response, jsonify, request
import time, os, re
from services.scrape_runner import start_scrape, status, LOG_FILE

bp = Blueprint("scrape", __name__, url_prefix="/api/scrape")

@bp.route("/start", methods=["POST"])
def start_endpoint():
    site_id = (request.get_json() or {}).get("site_id")
    args = ["--site", site_id] if site_id else []
    res = start_scrape(args)
    if not res.get("started"):
        return jsonify({"error": "Scraper already running"}), 409
    return jsonify(res)

@bp.route("/status", methods=["GET"])
def status_endpoint():
    return jsonify(status())

# --- Add this helper ---
_level_width = 8  # fits CRITICAL (8), aligns everything else (INFO, ERROR, WARNING)
_line_re = re.compile(
    r"""^
    (?P<date>\d{4}-\d{2}-\d{2})\s+
    (?P<h>\d{2}):(?P<m>\d{2})                # HH:MM
    (?::\d{2}(?:[.,]\d{3})?)?                # optional :SS,mmm
    \s+\|\s+(?P<lvl>[A-Z]+)\s+\|\s+
    (?P<logger>[^|]+)\s+\|\s+
    (?P<msg>.*)$
    """,
    re.VERBOSE,
)

def _transform_line(raw: str) -> str:
    """
    Convert 'YYYY-MM-DD HH:MM:SS,mmm | LEVEL | LOGGER | message'
    ->      'YYYY-MM-DD HH:MM        | LEVEL···· | message'
    """
    m = _line_re.match(raw.rstrip("\r\n"))
    if not m:
        # Fall back to original if it doesn't match expected pattern
        return raw.rstrip("\r\n")
    date = m.group("date")
    hm   = f"{m.group('h')}:{m.group('m')}"
    lvl  = m.group("lvl").upper().ljust(_level_width)
    msg  = m.group("msg")
    return f"{date} {hm} | {lvl} | {msg}"

def _sse(line: str) -> bytes:
    return f"data: {line}\n\n".encode("utf-8", errors="ignore")
# -----------------------

@bp.route("/log/stream", methods=["GET"])
def stream_logs():
    def gen():
        with open(LOG_FILE, "rb", buffering=0) as f:
            try:
                f.seek(max(0, os.path.getsize(LOG_FILE) - 8192))
            except Exception:
                pass
            yield _sse("?? connected to log stream")
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
                            # decode -> transform -> send
                            txt = line.decode("utf-8", errors="ignore")
                            out = _transform_line(txt)
                            yield _sse(out)
                        except Exception:
                            # if anything goes wrong, send raw line
                            yield _sse(line.decode("utf-8", errors="ignore"))
    return Response(gen(), mimetype="text/event-stream")
