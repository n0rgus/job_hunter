#!/usr/bin/env python3
# _automation/input_trigger.py
# Robust watcher for JSON patch files in _input, with strict UTF-8 decode,
# size-stabilization, detailed diagnostics, backups, archiving, and a JSON patch engine
# that uses AST to locate functions/methods (incl. class methods, multiline signatures, decorators).

from __future__ import annotations
import os
import sys
import time
import json
import shutil
import hashlib
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Tuple, Dict, Any, Optional

# ---------- Configuration ----------
BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = (BASE_DIR / "_input").resolve()
ARCHIVE_OK = (INPUT_DIR / "_archive_ok").resolve()
ARCHIVE_ERR = (INPUT_DIR / "_archive_err").resolve()
LOG_DIR = (BASE_DIR / "_logs").resolve()
LOG_FILE = LOG_DIR / "input.trigger.log"

SCAN_INTERVAL_SEC = 0.5
STABILITY_RETRIES = 10
STABILITY_SLEEP_SEC = 0.2

# Accept JSON extensions you use
ALLOWED_EXTS = {".json", ".json.dif", ".dif.json"}

# ---------- Logging ----------
def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_FILE, maxBytes=512_000, backupCount=3, encoding="utf-8")
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

def console(msg: str) -> None:
    print(msg, flush=True)

# ---------- Utilities ----------
def sha256_head(path: Path, head_len: int = 16) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:head_len]

def hex_window(b: bytes, pos: int, radius: int = 24) -> str:
    start = max(0, pos - radius)
    end = min(len(b), pos + radius + 1)
    segment = b[start:end]
    return " ".join(f"{x:02X}" for x in segment)

def compute_line_col_from_offset(b: bytes, pos: int) -> Tuple[int, int]:
    line = b.count(b"\n", 0, pos) + 1
    last_nl = b.rfind(b"\n", 0, pos)
    col = (pos - (last_nl + 1)) if last_nl != -1 else pos
    return line, col

def show_text_context(text: str, line_no: int, pad: int = 2) -> str:
    lines = text.splitlines()
    start = max(1, line_no - pad)
    end = min(len(lines), line_no + pad)
    out = []
    for i in range(start, end + 1):
        prefix = ">>" if i == line_no else "  "
        out.append(f"{prefix} {i:6d}: {lines[i-1]}")
    return "\n".join(out)

def wait_until_stable(path: Path, tries: int = STABILITY_RETRIES, delay: float = STABILITY_SLEEP_SEC) -> bool:
    """Wait until file size stops changing and is non-zero."""
    last = -1
    for _ in range(tries):
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            time.sleep(delay)
            continue
        if size == last and size > 0:
            return True
        last = size
        time.sleep(delay)
    return False

def read_json_utf8_strict(path: Path) -> Dict[str, Any]:
    """Read JSON using strict UTF-8; if BOM present, utf-8-sig; log diagnostics if decoding fails."""
    raw = path.read_bytes()
    size = len(raw)
    logging.info("Reading: %s (size=%d bytes, sha256=%s…)", path, size, sha256_head(path))

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        line, col = compute_line_col_from_offset(raw, e.start)
        logging.error(
            "UTF-8 decode error in %s: byte 0x%02X at pos %d (approx %d:%d); reason=%s",
            path, raw[e.start], e.start, line, col, e.reason
        )
        logging.error("Hex context: %s", hex_window(raw, e.start, radius=32))
        preview = raw[max(0, e.start-48):min(size, e.start+48)]
        safe = "".join(chr(b) if 32 <= b < 127 else "." for b in preview)
        logging.error("ASCII preview: %s", safe)
        logging.warning("Retrying with utf-8-sig for BOM tolerance: %s", path)
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as e2:
            logging.error("Second decode failed as well: %s", e2)
            raise

    try:
        data = json.loads(text)
    except json.JSONDecodeError as je:
        logging.error(
            "JSON parse error in %s: %s at line %d, col %d (char %d)",
            path, je.msg, je.lineno, je.colno, je.pos
        )
        logging.error("Context:\n%s", show_text_context(text, je.lineno, pad=3))
        raise
    return data

# ---------- Patch Engine ----------
def apply_patch(patch: Dict[str, Any]) -> None:
    """
    Apply a JSON patch spec to a target file.

    Supported edit types:
      - replace:        replace a single line (1-based) with 'new' (validates 'original' if present)
      - insert_before:  insert 'new' before the given line (1-based)
      - insert_after:   insert 'new' after the given line (1-based)
      - replace_function: replace the entire body of a function/method named 'function_name'
      - replace_method_in_class: like replace_function, but scoped to 'class_name'

    The 'file' path in the patch is resolved relative to the repo root (parent of this _automation folder),
    and backslashes in the JSON are supported.
    """
    import ast
    from datetime import datetime

    # --- Validate patch shape ---
    if not isinstance(patch, dict):
        raise ValueError("Top-level JSON must be an object")
    required = {"file", "edits", "commit_message"}
    missing = required - set(patch.keys())
    if missing:
        raise ValueError(f"Missing required keys: {sorted(missing)}")
    if not isinstance(patch["edits"], list) or not patch["edits"]:
        raise ValueError("'edits' must be a non-empty list")

    # --- Resolve target file path relative to repo root (parent of _automation) ---
    target_rel = str(patch["file"]).replace("/", os.sep)
    REPO_ROOT = BASE_DIR.parent
    target_path = (REPO_ROOT / target_rel).resolve() if not os.path.isabs(target_rel) else Path(target_rel).resolve()
    if not target_path.exists():
        raise FileNotFoundError(f"Target file not found: {target_path}")

    # --- Load original text with multi-encoding fallback (legacy files may be cp1252/latin-1) ---
    detected_encoding = None
    read_error = None
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            text = target_path.read_text(encoding=enc)
            detected_encoding = enc
            break
        except UnicodeDecodeError as e:
            read_error = e
            continue
    if detected_encoding is None:
        raise UnicodeDecodeError(f"Failed to decode {target_path} with utf-8/utf-8-sig/cp1252/latin-1: {read_error}")  # type: ignore[arg-type]
    else:
        if detected_encoding not in ("utf-8", "utf-8-sig"):
            logging.warning("Target file %s decoded as %s (non-UTF-8). Changes will be written back as UTF-8.", target_path, detected_encoding)

    # Detect newline style
    newline = "\r\n" if "\r\n" in text else "\n"

    # Work with line array (keepends True for precise placement)
    lines = text.splitlines(keepends=True)

    def norm_newlines(s: str) -> str:
        # Normalize provided 'new' text to the file's newline style
        return s.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)

    def write_back():
        backups_dir = (BASE_DIR / "_backups")
        backups_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # Backup original using original encoding when possible
        backup_file = backups_dir / f"{target_path.name}.{stamp}.bak"
        try:
            backup_file.write_text(text, encoding=detected_encoding or "utf-8")
        except Exception:
            backup_file.write_bytes(text.encode(detected_encoding or "utf-8", errors="ignore"))
        # Write new content normalized to UTF-8
        new_text = "".join(lines)
        target_path.write_text(new_text, encoding="utf-8")

    # ---- AST helpers ----
    def _function_block_from_ast(source: str, func_name: str, class_name: Optional[str] = None) -> Tuple[int, int]:
        """
        Return (start_index, end_index_exclusive) for function/method by name (and optional class).
        Uses ast to support decorators and multi-line signatures.
        """
        tree = ast.parse(source)
        target_node = None

        if class_name:
            # Find the class first
            for node in tree.body:
                if isinstance(node, ast.ClassDef) and node.name == class_name:
                    for sub in node.body:
                        if isinstance(sub, ast.FunctionDef) and sub.name == func_name:
                            target_node = sub
                            break
                    break
        else:
            # Module-level or any first occurrence in the file
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == func_name:
                    target_node = node
                    break

        if target_node is None:
            raise ValueError(f"replace_function: def {func_name}(...) not found" + (f" in class {class_name}" if class_name else ""))

        # Python 3.8+ provides end_lineno; use it if available
        if hasattr(target_node, "lineno") and hasattr(target_node, "end_lineno") and target_node.end_lineno:
            start_idx = target_node.lineno - 1
            end_idx = target_node.end_lineno  # exclusive
            return start_idx, end_idx

        # Fallback: compute end by scanning indentation (rarely used in 3.11)
        start_idx = target_node.lineno - 1
        # Determine indent level of the 'def' line
        def_line = lines[start_idx]
        base_indent = len(def_line) - len(def_line.lstrip())
        end_idx = len(lines)
        for j in range(start_idx + 1, len(lines)):
            ln = lines[j]
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            cur_indent = len(ln) - len(ln.lstrip())
            if cur_indent <= base_indent and ln.lstrip().startswith(("def ", "class ")):
                end_idx = j
                break
        return start_idx, end_idx

    # ---- simple line edits ----
    def apply_replace(edit: Dict[str, Any]):
        line_no = int(edit.get("line", 0))
        if line_no < 1 or line_no > len(lines):
            raise IndexError(f"replace: line {line_no} out of range (1..{len(lines)})")
        cur = lines[line_no - 1].rstrip("\r\n")
        orig = edit.get("original")
        if orig is not None and cur != orig:
            logging.warning("replace: original mismatch at line %d. Expected %r, found %r. Proceeding.", line_no, orig, cur)
        new_text = norm_newlines(edit.get("new", ""))
        if not new_text.endswith(newline):
            new_text += newline
        lines[line_no - 1] = new_text

    def apply_insert(edit: Dict[str, Any], where: str):
        line_no = int(edit.get("line", 0))
        if line_no < 1 or line_no > len(lines) + 1:
            raise IndexError(f"{where}: line {line_no} out of range (1..{len(lines)+1})")
        new_text = norm_newlines(edit.get("new", ""))
        if not new_text.endswith(newline):
            new_text += newline
        idx = line_no - 1
        if where == "insert_before":
            lines[idx:idx] = [new_text]
        else:  # insert_after
            lines[idx+1:idx+1] = [new_text]

    # ---- function/method edits using AST ----
    def apply_replace_function(edit: Dict[str, Any]):
        func_name = edit.get("function_name")
        if not func_name:
            raise ValueError("replace_function: 'function_name' is required")
        new_body = edit.get("new_body")
        if not isinstance(new_body, list) or not all(isinstance(x, str) for x in new_body):
            raise ValueError("replace_function: 'new_body' must be a list of strings")
        source = "".join(lines)
        start_idx, end_idx = _function_block_from_ast(source, func_name, class_name=None)
        replacement = norm_newlines("\n".join(new_body))
        if not replacement.endswith(newline):
            replacement += newline
        lines[start_idx:end_idx] = [replacement]

    def apply_replace_method_in_class(edit: Dict[str, Any]):
        class_name = edit.get("class_name")
        func_name = edit.get("function_name")
        new_body = edit.get("new_body")
        if not class_name or not func_name:
            raise ValueError("replace_method_in_class: 'class_name' and 'function_name' are required")
        if not isinstance(new_body, list) or not all(isinstance(x, str) for x in new_body):
            raise ValueError("replace_method_in_class: 'new_body' must be a list of strings")
        source = "".join(lines)
        start_idx, end_idx = _function_block_from_ast(source, func_name, class_name=class_name)
        replacement = norm_newlines("\n".join(new_body))
        if not replacement.endswith(newline):
            replacement += newline
        lines[start_idx:end_idx] = [replacement]

    # --- Apply each edit ---
    for i, edit in enumerate(patch["edits"], start=1):
        et = edit.get("type")
        if et not in {"replace", "insert_before", "insert_after", "replace_function", "replace_method_in_class"}:
            raise ValueError(f"Edit #{i}: unsupported type '{et}'")
        logging.info("Applying edit #%d: %s", i, et)
        if et == "replace":
            apply_replace(edit)
        elif et == "insert_before":
            apply_insert(edit, where="insert_before")
        elif et == "insert_after":
            apply_insert(edit, where="insert_after")
        elif et == "replace_function":
            apply_replace_function(edit)
        elif et == "replace_method_in_class":
            apply_replace_method_in_class(edit)

    # --- Write changes (with backup) ---
    write_back()
    logging.info("Patch applied to %s — %s", target_path, patch.get("commit_message"))

# ---------- Processing one file ----------
def process_file(json_path: Path) -> Tuple[bool, str]:
    try:
        if not wait_until_stable(json_path):
            msg = f"File never stabilized: {json_path}"
            logging.warning(msg)
            return False, msg

        patch = read_json_utf8_strict(json_path)
        apply_patch(patch)
        return True, "Applied"
    except Exception as e:
        logging.exception("Failed processing %s: %s", json_path, e)
        return False, str(e)

# ---------- Main loop ----------
def main() -> None:
    setup_logging()
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    ARCHIVE_OK.mkdir(parents=True, exist_ok=True)
    ARCHIVE_ERR.mkdir(parents=True, exist_ok=True)

    console(f"👀 Watching for JSON files in {INPUT_DIR} ...")

    seen: Dict[Path, float] = {}  # path -> last mtime seen

    try:
        while True:
            for p in INPUT_DIR.iterdir():
                if not p.is_file():
                    continue
                if p.suffix.lower() not in ALLOWED_EXTS:
                    continue
                if ARCHIVE_OK in p.parents or ARCHIVE_ERR in p.parents:
                    continue

                mtime = p.stat().st_mtime
                if seen.get(p, -1) == mtime:
                    continue  # already handled this mtime
                seen[p] = mtime

                console(f"📄 Detected JSON file: {p}")
                ok, reason = process_file(p)

                # Move to archive
                dst_dir = ARCHIVE_OK if ok else ARCHIVE_ERR
                dst_dir.mkdir(parents=True, exist_ok=True)
                dst = dst_dir / p.name
                if dst.exists():
                    stem, suf = os.path.splitext(p.name)
                    i = 1
                    while True:
                        cand = dst_dir / f"{stem}.{i}{suf}"
                        if not cand.exists():
                            dst = cand
                            break
                        i += 1
                try:
                    shutil.move(str(p), str(dst))
                except Exception as move_err:
                    logging.exception("Failed to archive %s -> %s: %s", p, dst, move_err)

                if ok:
                    console(f"✅ Applied and archived to: {dst}")
                else:
                    console(f"❌ Error applying {p.name}: {reason}")
                    console(f"🗃️ Moved {p.name} to {dst_dir.name}.")

            time.sleep(SCAN_INTERVAL_SEC)
    except KeyboardInterrupt:
        console("👋 Stopped.")

# ---------- Entry ----------
if __name__ == "__main__":
    # Optional: allow a one-off file path for direct test runs
    if len(sys.argv) == 2:
        setup_logging()
        test_path = Path(sys.argv[1]).resolve()
        ok, reason = process_file(test_path)
        print("Result:", "OK" if ok else f"ERROR: {reason}")
        sys.exit(0 if ok else 1)
    main()
