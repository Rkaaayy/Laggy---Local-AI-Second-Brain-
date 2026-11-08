import os
import sys
import time
import json
import uuid
import base64
import sqlite3
import logging
from pathlib import Path
from importlib import import_module
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

watcher_mod = import_module("07_file_watcher")

# Ensure imports work regardless of working directory
SRC_DIR = Path(__file__).resolve().parent
BASE_DIR = SRC_DIR.parent
UI_HTML_PATH = SRC_DIR / "ui.html"
DATA_DIR = BASE_DIR / "data"
CHAT_DB_PATH = DATA_DIR / "chat_history.db"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

prompt_mod = import_module("01_prompt_and_memory")
rag_mod = import_module("02_rag_ingestion")
mcp_mod = import_module("03_mcp_server")
auth_mod = import_module("04_auth_rbac")
guard_mod = import_module("05_guardrails")
router_mod = import_module("06_router_cache")

# =====================================================================
# OBSERVABILITY
# =====================================================================
os.environ.pop("PHOENIX_API_KEY", None)
os.environ.pop("PHOENIX_CLIENT_Headers", None)
os.environ.pop("OTEL_EXPORTER_OTLP_HEADERS", None)
logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter").setLevel(logging.CRITICAL)

try:
    from phoenix.otel import register
    from opentelemetry import trace
    tracer_provider = register(project_name="laggy-local-second-brain", endpoint="http://127.0.0.1:6006/v1/traces")
    tracer = trace.get_tracer("laggy.orchestrator")
    PHOENIX_ENABLED = True
except Exception as e:
    print(f"[!] Phoenix tracing fallback (offline): {e}")
    PHOENIX_ENABLED = False


# =====================================================================
# MASTER PIPELINE (Non-Streaming / Testing)
# =====================================================================
def process_laggy_request(user_query: str, session_jwt: str, session_id: str = None) -> dict:
    start_ts = time.perf_counter()

    def _execute_pipeline(span=None):
        nonlocal session_id
        session_info = auth_mod.verify_token_and_permissions(session_jwt)
        user_role = session_info["role"]
        
        if span:
            span.set_attribute("user.role", user_role)
            span.set_attribute("input.query", user_query)

        # 1. Input Guardrail (Bypass for Vault Admin to speed up inference)
        if user_role != "vault_admin":
            guard_check = guard_mod.check_input_guardrail(user_query)
            if not guard_check["allowed"]:
                return {"role": user_role, "source": "INPUT_GUARDRAIL_BLOCK", "model_used": "llama-guard3:1b", "latency_sec": round(time.perf_counter() - start_ts, 3), "response": f"Security Block: {guard_check['reason']}"}

        # 2. Manage Chat Session & History
        if not session_id:
            session_id = str(uuid.uuid4())
            title = " ".join(user_query.split()[:5]) + "..."
            with sqlite3.connect(CHAT_DB_PATH) as conn:
                conn.execute("INSERT INTO sessions (id, title, role, created_at) VALUES (?, ?, ?, ?)", (session_id, title, user_role, time.time()))

        history = []
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            rows = conn.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)).fetchall()
            for r in rows:
                history.append({"role": r[0], "content": r[1]})

        # 3. Model Router & RAG MCP
        routed_res = router_mod.routed_query(user_query, session_jwt, chat_history=history[-10:], current_session_id=session_id)

        # 4. Output Guardrail
        safe_response = guard_mod.redact_sensitive_output(routed_res["response"], user_role=user_role)
        total_latency = round(time.perf_counter() - start_ts, 3)

        # 5. Save Turn to DB
        with sqlite3.connect(CHAT_DB_PATH) as conn:
            conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, "user", user_query))
            conn.execute("INSERT INTO messages (session_id, role, content, source, model_used, latency_sec) VALUES (?, ?, ?, ?, ?, ?)", 
                         (session_id, "assistant", safe_response, routed_res["source"], routed_res["model_used"], total_latency))

        return {
            "session_id": session_id,
            "role": user_role,
            "source": routed_res["source"],
            "model_used": routed_res["model_used"],
            "latency_sec": total_latency,
            "response": safe_response,
        }

    if PHOENIX_ENABLED:
        with tracer.start_as_current_span("Laggy_Agent_Turn") as span:
            return _execute_pipeline(span)
    return _execute_pipeline()

process_aegis_request = process_laggy_request


# =====================================================================
# FASTAPI SERVER & ENDPOINTS
# =====================================================================
app = FastAPI(title="Laggy Local Second Brain")
app.mount("/static", StaticFiles(directory=str(DATA_DIR)), name="static")

class AuthRequest(BaseModel):
    totp_code: str | None = None

class ChatRequest(BaseModel):
    query: str
    token: str
    session_id: str | None = None

class UploadRequest(BaseModel):
    filename: str
    content_b64: str
    clearance: str
    token: str

@app.on_event("startup")
def startup_event():
    # Init Chat DB
    CHAT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, title TEXT, role TEXT, created_at REAL)")
        conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT, content TEXT, source TEXT, model_used TEXT, latency_sec REAL)")

    try:
        router_mod.redis_client.flushall()
    except Exception:
        pass
    try:
        rag_mod.client.delete_collection(rag_mod.COLLECTION_NAME)
    except Exception:
        pass

    rag_mod.init_vector_db()
    rag_mod.ingest_folder(rag_mod.STANDARD_DIR, clearance_level="standard")
    rag_mod.ingest_folder(rag_mod.VAULT_DIR, clearance_level="vault")
    watcher_mod.start_background_watcher()


@app.post("/api/auth")
def authenticate(req: AuthRequest):
    try:
        code = auth_mod.get_current_totp_code() if req.totp_code == "UNLOCK" else req.totp_code
        token = auth_mod.create_session_token("Hrishikesh", totp_code=code)
        info = auth_mod.verify_token_and_permissions(token)
        return {"token": token, "role": info["role"], "live_totp_hint": auth_mod.get_current_totp_code()}
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    try:
        return process_laggy_request(req.query, req.token, req.session_id)
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.post("/api/chat_stream")
def chat_stream_endpoint(req: ChatRequest):
    """Streaming endpoint for the Web UI."""
    try:
        session_info = auth_mod.verify_token_and_permissions(req.token)
        user_role = session_info["role"]
        
        # Input Guardrail (Bypass for Vault Admin to speed up inference)
        if user_role != "vault_admin":
            guard_check = guard_mod.check_input_guardrail(req.query)
            if not guard_check["allowed"]:
                def block_gen():
                    yield f"data: {json.dumps({'token': f'Security Block: {guard_check['reason']}'})}\n\n"
                return StreamingResponse(block_gen(), media_type="text/event-stream")

        def event_generator():
            start_ts = time.perf_counter()
            session_id = req.session_id
            
            # 1. DB Session Management
            if not session_id:
                session_id = str(uuid.uuid4())
                title = " ".join(req.query.split()[:5]) + "..."
                with sqlite3.connect(CHAT_DB_PATH) as conn:
                    conn.execute("INSERT INTO sessions (id, title, role, created_at) VALUES (?, ?, ?, ?)", (session_id, title, user_role, time.time()))
            
            history = []
            with sqlite3.connect(CHAT_DB_PATH) as conn:
                rows = conn.execute("SELECT role, content FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)).fetchall()
                history = [{"role": r[0], "content": r[1]} for r in rows]

            # Push session ID to frontend immediately
            yield f"data: {json.dumps({'session_id': session_id})}\n\n"

            # 2. Router classification
            q_words = set(req.query.lower().split())
            is_followup = bool(history) and bool(q_words & {"this", "that", "it", "more", "instead", "change", "no", "also"})
            
            cached_ans, query_vec, _ = (None, None, 0.0)
            if not is_followup:
                cached_ans, query_vec, _ = router_mod.check_semantic_cache(req.query, user_role)
            
            full_response = ""
            source_used = ""
            model_used = ""
            
            # 3. Model Generation
            if cached_ans:
                full_response = cached_ans
                source_used = "REDIS_SEMANTIC_CACHE"
                model_used = "NONE"
                yield f"data: {json.dumps({'token': full_response})}\n\n"
            else:
                target_model = router_mod.classify_and_route(req.query) if not is_followup else router_mod.FRONTIER_LOCAL_MODEL
                source_used = "LIVE_INFERENCE"
                model_used = target_model
                
                if target_model == router_mod.SMALL_FAST_MODEL:
                    # Pass the memory_context retrieved in the router to the generator
                    memory_context = router_mod.retrieve_cross_chat_memory(req.query, session_id, user_role)
                    sys_prompt = prompt_mod.load_system_prompt(memory_context=memory_context)
                    for token in router_mod.stream_fast_model(req.query, sys_prompt):
                        full_response += token
                        yield f"data: {json.dumps({'token': token})}\n\n"
                else:
                    # Pass the memory_context down through the agent
                    memory_context = router_mod.retrieve_cross_chat_memory(req.query, session_id, user_role)
                    full_response = mcp_mod.run_agent_turn(req.query, session_jwt=req.token, chat_history=history[-10:], memory_context=memory_context)
                    yield f"data: {json.dumps({'token': full_response})}\n\n"
                    
                if not is_followup and query_vec is not None:
                    router_mod.store_in_semantic_cache(req.query, query_vec, full_response, user_role)

            # 4. Cleanup & DB Storage
            safe_memory_response = guard_mod.redact_sensitive_output(full_response, user_role=user_role)
            latency = round(time.perf_counter() - start_ts, 3)

            with sqlite3.connect(CHAT_DB_PATH) as conn:
                conn.execute("INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)", (session_id, "user", req.query))
                conn.execute("INSERT INTO messages (session_id, role, content, source, model_used, latency_sec) VALUES (?, ?, ?, ?, ?, ?)", 
                             (session_id, "assistant", safe_memory_response, source_used, model_used, latency))
            
            yield f"data: {json.dumps({'meta': {'source': source_used, 'model': model_used, 'latency': latency}})}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@app.get("/api/sessions")
def get_sessions(token: str):
    session_info = auth_mod.verify_token_and_permissions(token)
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        rows = conn.execute("SELECT id, title FROM sessions WHERE role = ? ORDER BY created_at DESC", (session_info["role"],)).fetchall()
    return [{"id": r[0], "title": r[1]} for r in rows]


@app.get("/api/sessions/{session_id}")
def get_session_chat(session_id: str, token: str):
    session_info = auth_mod.verify_token_and_permissions(token)
    with sqlite3.connect(CHAT_DB_PATH) as conn:
        rows = conn.execute("SELECT role, content, source, model_used, latency_sec FROM messages WHERE session_id = ? ORDER BY id ASC", (session_id,)).fetchall()
    return [{"role": r[0], "content": r[1], "source": r[2], "model_used": r[3], "latency_sec": r[4]} for r in rows]


@app.post("/api/upload")
def upload_file(req: UploadRequest):
    """Saves base64 file data cleanly without requiring python-multipart."""
    session = auth_mod.verify_token_and_permissions(req.token)
    if req.clearance == "vault" and session["role"] != "vault_admin":
        raise HTTPException(status_code=403, detail="Need 2FA Vault Unlock to upload confidential files.")
    
    folder = rag_mod.VAULT_DIR if req.clearance == "vault" else rag_mod.STANDARD_DIR
    folder.mkdir(parents=True, exist_ok=True)
    file_path = folder / req.filename
    
    with open(file_path, "wb") as f:
        f.write(base64.b64decode(req.content_b64))
        
    return {"status": "success", "filename": req.filename}


@app.get("/api/status")
def get_system_status():
    files_seen = {}
    try:
        scroll_res = rag_mod.client.scroll(collection_name=rag_mod.COLLECTION_NAME, limit=100, with_payload=True, with_vectors=False)[0]
        for point in scroll_res:
            p = point.payload
            files_seen[p.get("filename", "unknown")] = p.get("clearance", "standard")
    except Exception:
        pass
    file_list = [{"filename": k, "clearance": v} for k, v in files_seen.items()]
    tasks = []
    try:
        db_path = BASE_DIR / "data" / "personal_tasks.db"
        if db_path.exists():
            with sqlite3.connect(db_path) as conn:
                tasks = [{"id": r[0], "task": r[1], "status": r[2]} for r in conn.execute("SELECT id, task, status FROM tasks").fetchall()]
    except Exception:
        pass
    return {"files": file_list, "tasks": tasks}


@app.get("/", response_class=HTMLResponse)
def web_ui():
    if not UI_HTML_PATH.exists():
        return HTMLResponse("<h3>Error: src/ui.html not found.</h3>", status_code=404)
    return HTMLResponse(UI_HTML_PATH.read_text(encoding="utf-8"))


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)