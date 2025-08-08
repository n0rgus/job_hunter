import time
import os
import json
import shutil
import logging
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from apply_edits import apply_edits  # must support single file patch application

WATCH_DIR = r"..\\_input"
REPO_DIR = r"..\\"
ARCHIVE_DIR = os.path.join(WATCH_DIR, "archive")
LOG_FILE = "input_trigger.log"

# Ensure archive directory exists
os.makedirs(ARCHIVE_DIR, exist_ok=True)

# Setup logging
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def apply_single_patch(patch_data):
    if "file" not in patch_data or "edits" not in patch_data:
        raise ValueError("Missing required 'file' or 'edits' field in JSON block.")
    apply_edits(patch_data, repo_dir=REPO_DIR)

class ChangeHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".json"):
            json_path = event.src_path
            print(f"📄 Detected JSON file: {json_path}")
            logging.info(f"Detected new JSON file: {json_path}")

            # Wait for file to stabilize
            for _ in range(10):
                try:
                    with open(json_path, 'r'):
                        break
                except Exception:
                    time.sleep(0.5)
            else:
                print("❌ File never stabilized, skipping.")
                logging.warning(f"File {json_path} never stabilized, skipping.")
                return

            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                # If raw list, wrap in fallback structure
                if isinstance(data, list):
                    logging.warning("⚠️ JSON is a raw list — auto-wrapping in 'changes'.")
                    data = { "changes": data }

                commit_msg = data.get("commit_message", "Automated code update")

                if "files" in data and "changes" not in data:
                    data["changes"] = data["files"]

                if "changes" in data:
                    for change in data["changes"]:
                        change.setdefault("commit_message", commit_msg)
                        apply_single_patch(change)
                elif "file" in data and "edits" in data:
                    apply_single_patch(data)
                else:
                    raise ValueError("JSON must contain either 'file' + 'edits' or a 'changes' list.")

                print(f"✅ Changes applied from {json_path}")
                logging.info(f"Changes successfully applied from {json_path}")
            except Exception as e:
                print(f"❌ Error applying {json_path}: {e}")
                logging.error(f"Error applying {json_path}: {e}", exc_info=True)
            finally:
                try:
                    archive_path = os.path.join(ARCHIVE_DIR, os.path.basename(json_path))
                    shutil.move(json_path, archive_path)
                    print(f"🗃️ Moved {json_path} to archive.")
                    logging.info(f"Archived {json_path} to {archive_path}")
                except Exception as cleanup_err:
                    print(f"⚠️ Error archiving file: {cleanup_err}")
                    logging.warning(f"Failed to archive {json_path}: {cleanup_err}", exc_info=True)

if __name__ == "__main__":
    event_handler = ChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, path=WATCH_DIR, recursive=False)
    observer.start()
    print(f"👀 Watching for JSON files in {WATCH_DIR}...")
    logging.info(f"Started watcher on {WATCH_DIR}")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        logging.info("File watcher stopped by user.")
    observer.join()