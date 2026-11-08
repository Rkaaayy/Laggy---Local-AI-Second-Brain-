import json
import time
import sqlite3
from pathlib import Path
from importlib import import_module
import numpy as np
import ollama
import redis

BASE_DIR = Path(__file__).resolve().parent.parent
CHAT_DB_PATH = BASE_DIR / "data" / "chat_history.db"

prompt_mod = import_module("01_prompt_and_memory")
mcp_mod = import_module("03_mcp_server")
auth_mod = import_module("04_auth_rbac")

REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
CACHE_KEY_PREFIX = "aegis_semantic_cache:"
SIMILARITY_THRESHOLD = 0.86  

EMBED_MODEL = "nomic-embed-text"
SMALL_FAST_MODEL = "qwen2.5:7b"
FRONTIER_LOCAL_MODEL = "qwen2.5:7b"

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    a = np.array(vec_a)
    b = np.array(vec_b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))

def retrieve_cross_chat_memory(query: str, current_session_id: str, user_role: str) -> str:
    try:
        query_vec = ollama.embed(model=EMBED_MODEL, input=query)["embeddings"][0]
        role_cache_key = f"{CACHE_KEY_PREFIX}{user_role}"
        cached_entries = redis_client.lrange(role_cache_key, 0, -1)
        
        relevant_memories = []
        for item_json in cached_entries:
            item = json.loads(item_json)
            if item["query"] == query: continue
                
            score = cosine_similarity(query_vec, item["vector"])
            if score > 0.65: 
                relevant_memories.append(f"User previously asked: '{item['query']}'\nLaggy answered: '{item['response'][:300]}...'")
        
        if relevant_memories:
            return "\n\n".join(relevant_memories[:3])
    except Exception as e:
        print(f"[*] Memory retrieval error: {e}")
    return ""

def check_semantic_cache(query: str, user_role: str) -> tuple[str | None, list[float], float]:
    query_vec = ollama.embed(model=EMBED_MODEL, input=query)["embeddings"][0]
    role_cache_key = f"{CACHE_KEY_PREFIX}{user_role}"
    cached_entries = redis_client.lrange(role_cache_key, 0, -1)
    best_match, best_score = None, 0.0

    for item_json in cached_entries:
        item = json.loads(item_json)
        score = cosine_similarity(query_vec, item["vector"])
        if score > best_score:
            best_score = score
            best_match = item

    if best_match and best_score >= SIMILARITY_THRESHOLD:
        return best_match["response"], query_vec, best_score
    return None, query_vec, best_score

def store_in_semantic_cache(query: str, query_vec: list[float], response: str, user_role: str):
    res_lower = response.lower()
    if any(x in res_lower for x in ["do not see that in your indexed files", "retrieved_local_context", "no relevant local files"]):
        return
    role_cache_key = f"{CACHE_KEY_PREFIX}{user_role}"
    entry = {"query": query, "vector": query_vec, "response": response, "timestamp": time.time()}
    redis_client.rpush(role_cache_key, json.dumps(entry))

AGENT_KEYWORDS = [
    "file", "contract", "pin", "workout", "split", "diet", "meal",
    "task", "to-do", "todo", "add", "list", "save", "note", "markdown",
    "start date", "position", "vault", "indexed", "schedule",
    "search", "web", "internet", "look up", "lookup", "online", "fetch", "rate", "rupee", "euro",
    "run", "command", "terminal", "execute", "ls", "dir", "check", "folder",
    "skill", "learn", "teach", "routine", "remember how to"
]

def classify_and_route(query: str) -> str:
    q_lower = query.lower()
    if any(kw in q_lower for kw in AGENT_KEYWORDS) or len(query.split()) > 18:
        return FRONTIER_LOCAL_MODEL
    return SMALL_FAST_MODEL

def stream_fast_model(user_query: str, sys_prompt: str, chat_history: list = None):
    messages = [{"role": "system", "content": sys_prompt}]
    if chat_history:
        messages.extend(chat_history)
    messages.append({"role": "user", "content": user_query})
    
    stream_res = ollama.chat(
        model=SMALL_FAST_MODEL,
        messages=messages,
        stream=True
    )
    for chunk in stream_res:
        yield chunk['message']['content']

def routed_query(user_query: str, session_jwt: str, chat_history: list = None, current_session_id: str = None, active_skill: str = None) -> dict:
    start_time = time.perf_counter()
    session = auth_mod.verify_token_and_permissions(session_jwt)
    user_role = session["role"]

    q_words = set(user_query.lower().split())
    is_followup = bool(chat_history) and bool(q_words & {"this", "that", "it", "more", "instead", "change", "no", "also", "yes", "approved", "run"})

    cached_ans, query_vec, sim_score = (None, None, 0.0)
    if not is_followup:
        cached_ans, query_vec, sim_score = check_semantic_cache(user_query, user_role)

    if cached_ans:
        elapsed = time.perf_counter() - start_time
        return {"source": f"REDIS_SEMANTIC_CACHE", "model_used": "NONE", "latency_sec": round(elapsed, 3), "response": cached_ans}

    memory_context = retrieve_cross_chat_memory(user_query, current_session_id, user_role)
    target_model = classify_and_route(user_query) if not is_followup else FRONTIER_LOCAL_MODEL

    if target_model == SMALL_FAST_MODEL:
        sys_prompt = prompt_mod.load_system_prompt(memory_context=memory_context, active_skill=active_skill)
        res = ollama.chat(
            model=SMALL_FAST_MODEL,
            messages=[{"role": "system", "content": sys_prompt}, {"role": "user", "content": user_query}],
        )
        answer = res["message"]["content"]
    else:
        answer = mcp_mod.run_agent_turn(user_query, session_jwt=session_jwt, chat_history=chat_history, memory_context=memory_context, active_skill=active_skill)

    if not is_followup and query_vec is not None:
        store_in_semantic_cache(user_query, query_vec, answer, user_role)

    elapsed = time.perf_counter() - start_time
    return {"source": "LIVE_INFERENCE", "model_used": target_model, "latency_sec": round(elapsed, 3), "response": answer}