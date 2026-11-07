import uuid
from pathlib import Path
from importlib import import_module
import ollama
from pypdf import PdfReader
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
)

# Automatically finds your 'local-second-brain' root folder
BASE_DIR = Path(__file__).resolve().parent.parent
STANDARD_DIR = BASE_DIR / "data" / "standard_docs"
VAULT_DIR = BASE_DIR / "data" / "vault_docs"

# Strictly connect to localhost (127.0.0.1)
QDRANT_URL = "http://127.0.0.1:6333"
COLLECTION_NAME = "personal_knowledge"
EMBED_MODEL = "bge-m3"

client = QdrantClient(url=QDRANT_URL)

def init_vector_db():
    """Creates the Qdrant collection if it doesn't exist yet (bge-m3 uses 1024 dimensions)."""
    collections = [c.name for c in client.get_collections().collections]
    if COLLECTION_NAME not in collections:
        client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
        )
        print(f"[+] Created Qdrant collection: {COLLECTION_NAME}")

def read_file_text(file_path: Path) -> str:
    """Extracts plain text from TXT, MD, PY, or PDF files."""
    if file_path.suffix.lower() == ".pdf":
        reader = PdfReader(str(file_path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    return file_path.read_text(encoding="utf-8", errors="ignore")

def chunk_text(text: str, chunk_size: int = 500, overlap: int = 100) -> list[str]:
    """Splits long documents into overlapping character chunks so context isn't cut in half."""
    chunks = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks

def ingest_folder(folder_path: Path, clearance_level: str):
    """Chunks, embeds with bge-m3 on RTX 3070, and saves to Qdrant with clearance tags."""
    if not folder_path.exists():
        print(f"  [!] Folder not found: {folder_path}")
        return

    points = []
    for file_path in folder_path.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in [".txt", ".md", ".pdf", ".py"]:
            text = read_file_text(file_path)
            chunks = chunk_text(text)
            for idx, chunk in enumerate(chunks):
                response = ollama.embed(model=EMBED_MODEL, input=chunk)
                vector = response["embeddings"][0]

                point_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{file_path.name}_{idx}"))

                points.append(
                    PointStruct(
                        id=point_id,
                        vector=vector,
                        payload={
                            "filename": file_path.name,
                            "text": chunk,
                            "clearance": clearance_level,
                            "chunk_index": idx,
                        },
                    )
                )
            print(f"  -> Indexed {file_path.name} ({len(chunks)} chunks) [Clearance: {clearance_level}]")

    if points:
        client.upsert(collection_name=COLLECTION_NAME, points=points)
    else:
        print(f"  [!] No .txt, .md, .pdf, or .py files found inside {folder_path}")

def retrieve_context(query: str, allowed_clearances: list[str] = None, top_k: int = 4) -> str:
    """Embeds the user's question and fetches matching chunks from Qdrant with robust formatting."""
    if allowed_clearances is None:
        allowed_clearances = ["standard"]

    try:
        query_vec = ollama.embed(model=EMBED_MODEL, input=query)["embeddings"][0]
    except Exception as e:
        print(f"  [!] Embedding error: {e}")
        return ""

    search_filter = Filter(
        should=[
            FieldCondition(key="clearance", match=MatchValue(value=level))
            for level in allowed_clearances
        ]
    )

    # 1. Vector similarity search
    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vec,
        query_filter=search_filter,
        limit=top_k,
    ).points

    formatted_chunks = []
    
    # 2. Add vector search results with clean bracket formatting
    for hit in results:
        p = hit.payload
        if p.get("clearance") in allowed_clearances:
            chunk_str = f"[{p.get('filename', 'unknown')} | Clearance: {p.get('clearance', 'standard')}]\n{p.get('text', '')}"
            if chunk_str not in formatted_chunks:
                formatted_chunks.append(chunk_str)

    # 3. Keyword / Filename fallback (guarantees project_roadmap.txt is found when asking about roadmap)
    q_lower = query.lower()
    try:
        scroll_res = client.scroll(
            collection_name=COLLECTION_NAME,
            limit=50,
            with_payload=True,
            with_vectors=False,
        )[0]
        for point in scroll_res:
            p = point.payload
            fname = p.get("filename", "").lower()
            if any(term in q_lower for term in ["roadmap", "workout", "contract", "diet", "task", "minimap", "thesis"]):
                if fname in q_lower or any(w in q_lower for w in fname.replace(".txt", "").split("_")):
                    if p.get("clearance") in allowed_clearances:
                        chunk_str = f"[{p.get('filename')} | Clearance: {p.get('clearance')}]\n{p.get('text', '')}"
                        if chunk_str not in formatted_chunks:
                            formatted_chunks.insert(0, chunk_str) # Prioritize keyword matches at the top
    except Exception:
        pass

    if not formatted_chunks:
        return "No relevant local files retrieved."

    return "\n\n".join(formatted_chunks[:top_k])