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
            print(f"?? Detected {TARGET_FILE}, waiting for write to complete...")
            
            # Retry up to 10 times with 0.5s delay
            for attempt in range(10):
                try:
                    if os.path.exists(event.src_path):
                        # Try opening the file to confirm it's no longer locked
                        with open(event.src_path, 'r'):
                            break  # Success!
                    time.sleep(0.5)
                except Exception:
                    time.sleep(0.5)
            else:
                print("? File was never fully available. Skipping.")
                return

            try:
                apply_edits(event.src_path, repo_dir="..\\")
                print("? Changes applied successfully!")
            except Exception as e:
                print(f"? Error applying changes: {e}")
            finally:
                try:
                    os.remove(event.src_path)
                    print("??? Cleaned up changes file.")
                except FileNotFoundError:
                    print("?? Tried to delete changes.json, but it vanished first.")

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
