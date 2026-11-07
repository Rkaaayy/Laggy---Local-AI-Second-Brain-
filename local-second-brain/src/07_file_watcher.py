import time
from pathlib import Path
from importlib import import_module
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

rag_mod = import_module("02_rag_ingestion")
BASE_DIR = Path(__file__).resolve().parent.parent
STANDARD_DIR = BASE_DIR / "data" / "standard_docs"
VAULT_DIR = BASE_DIR / "data" / "vault_docs"


class PersonalKnowledgeHandler(FileSystemEventHandler):
    """Watches data folders for new or modified files and indexes them automatically."""

    def on_created(self, event):
        if event.is_directory:
            return
        self._process_event(event.src_path, action="CREATED")

    def on_modified(self, event):
        if event.is_directory:
            return
        self._process_event(event.src_path, action="MODIFIED")

    def _process_event(self, src_path: str, action: str):
        path = Path(src_path)
        if path.suffix.lower() not in [".txt", ".md", ".pdf", ".py"]:
            return

        try:
            relative = path.relative_to(BASE_DIR / "data")
            clearance = "vault" if "vault_docs" in relative.parts else "standard"
        except ValueError:
            clearance = "standard"

        print(f"\n[* WATCHER DETECTED FILE {action}] -> {path.name} (Clearance: {clearance})")
        time.sleep(0.5)

        try:
            rag_mod.init_vector_db()
            folder = VAULT_DIR if clearance == "vault" else STANDARD_DIR
            rag_mod.ingest_folder(folder, clearance_level=clearance)
            print(f"[+ AUTO-INDEXED] Successfully indexed {path.name} into Qdrant!")
        except Exception as e:
            print(f"[- ERROR] Failed to auto-index {path.name}: {e}")


def start_background_watcher():
    event_handler = PersonalKnowledgeHandler()
    observer = Observer()

    if STANDARD_DIR.exists():
        observer.schedule(event_handler, path=str(STANDARD_DIR), recursive=True)
    if VAULT_DIR.exists():
        observer.schedule(event_handler, path=str(VAULT_DIR), recursive=True)

    observer.daemon = True
    observer.start()
    print("[*] Background File Watcher active on data/standard_docs & data/vault_docs")
    return observer