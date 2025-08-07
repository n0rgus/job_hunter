import time
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from apply_edits import apply_edits  # Your script from before

WATCH_DIR = r"..\_input"
TARGET_FILE = "changes.json"

class ChangeHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith(".json"):
            json_path = event.src_path
            print(f"?? Detected JSON file: {json_path}")

            # Wait for file to stabilize
            for _ in range(10):
                try:
                    with open(json_path, 'r'):
                        break
                except Exception:
                    time.sleep(0.5)
            else:
                print("? File never stabilized, skipping.")
                return

            try:
                apply_edits(json_path, repo_dir="..\\")
                print(f"? Changes applied from {json_path}")
            except Exception as e:
                print(f"? Error applying {json_path}: {e}")
            finally:
                try:
                    os.remove(json_path)
                    print(f"??? Cleaned up {json_path}")
                except FileNotFoundError:
                    print(f"?? File {json_path} not found during cleanup.")

if __name__ == "__main__":
    event_handler = ChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, path=WATCH_DIR, recursive=False)
    observer.start()
    print(f"?? Watching for JSON files in {WATCH_DIR}...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
