import time
import os
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from apply_edits import apply_edits  # Your script from before

WATCH_DIR = r"..\_input"
TARGET_FILE = "changes.json"

class ChangeHandler(FileSystemEventHandler):
    def on_created(self, event):
        if not event.is_directory and os.path.basename(event.src_path) == TARGET_FILE:
            print(f"?? Detected {TARGET_FILE}, applying changes...")
            try:
                apply_edits(event.src_path, repo_dir="..\\repo")
                print("? Changes applied successfully!")
            except Exception as e:
                print(f"? Error applying changes: {e}")
            finally:
                # Optional: delete or archive file after use
                os.remove(event.src_path)
                print("??? Cleaned up changes file.")

if __name__ == "__main__":
    event_handler = ChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, path=WATCH_DIR, recursive=False)
    observer.start()
    print(f"?? Watching for {TARGET_FILE} in {WATCH_DIR}...")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()
