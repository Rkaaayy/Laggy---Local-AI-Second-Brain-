import json
import time
from importlib import import_module
import numpy as np
import ollama
import redis

# Load our previous modules
prompt_mod = import_module("01_prompt_and_memory")
mcp_mod = import_module("03_mcp_server")
auth_mod = import_module("04_auth_rbac")

# Strictly connect to local Redis (127.0.0.1:6379)
REDIS_HOST = "127.0.0.1"
REDIS_PORT = 6379
CACHE_KEY_PREFIX = "aegis_semantic_cache:"
SIMILARITY_THRESHOLD = 0.86  # Cosine similarity threshold for a semantic hit

EMBED_MODEL = "bge-m3"
SMALL_FAST_MODEL = "qwen2.5:3b"
FRONTIER_LOCAL_MODEL = "qwen2.5:7b"

redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


# =====================================================================
# 1. REDIS SEMANTIC CACHE (Role-Isolated Vector Similarity)
# =====================================================================
def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Computes cosine similarity between two 1024-dim bge-m3 vectors."""
    a = np.array(vec_a)
    b = np.array(vec_b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def check_semantic_cache(query: str, user_role: str) -> tuple[str | None, list[float], float]:
    """
    Embeds the query and checks Redis for semantically similar past questions
    within the SAME RBAC role tier.
    """
    query_vec = ollama.embed(model=EMBED_MODEL, input=query)["embeddings"][0]
    role_cache_key = f"{CACHE_KEY_PREFIX}{user_role}"

    cached_entries = redis_client.lrange(role_cache_key, 0, -1)
    best_match = None
    best_score = 0.0

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
    """Saves the query vector and answer in Redis under the user's RBAC role namespace."""
    res_lower = response.lower()
    if (
        "do not see that in your indexed files" in res_lower
        or "retrieved_local_context" in res_lower
        or "no relevant local files" in res_lower
    ):
        return

    role_cache_key = f"{CACHE_KEY_PREFIX}{user_role}"
    entry = {
        "query": query,
        "vector": query_vec,
        "response": response,
        "timestamp": time.time(),
    }
    redis_client.rpush(role_cache_key, json.dumps(entry))


# =====================================================================
# 2. INTELLIGENT MODEL ROUTER (3B Fast Model vs. 7B Frontier Agent)
# =====================================================================
AGENT_KEYWORDS = [
    "file", "contract", "pin", "workout", "split", "diet", "meal",
    "task", "to-do", "todo", "add", "list", "save", "note", "markdown",
    "start date", "position", "vault", "indexed", "schedule",
]


def classify_and_route(query: str) -> str:
    """
    Routes queries between the 3B Fast Model and the 7B Frontier Agent:
    - Personal file lookups, MCP tool actions, or deep reasoning -> qwen2.5:7b
    - Casual chat, greetings, or general knowledge -> qwen2.5:3b
    """
    q_lower = query.lower()

    if any(kw in q_lower for kw in AGENT_KEYWORDS) or len(query.split()) > 18:
        return FRONTIER_LOCAL_MODEL

    return SMALL_FAST_MODEL


def routed_query(user_query: str, session_jwt: str, chat_history: list = None) -> dict:
    """End-to-end execution: RBAC Check -> Semantic Cache -> Model Router -> LLM/MCP."""
    start_time = time.perf_counter()

    session = auth_mod.verify_token_and_permissions(session_jwt)
    user_role = session["role"]

    # Check if this is a follow-up referring to previous messages ("this", "that", "it", "instead")
    q_words = set(user_query.lower().split())
    is_followup = bool(chat_history) and bool(q_words & {"this", "that", "it", "more", "instead", "change", "no", "also"})

    # Only use Redis Semantic Cache for standalone questions, not context-dependent follow-ups
    cached_ans, query_vec, sim_score = (None, None, 0.0)
    if not is_followup:
        cached_ans, query_vec, sim_score = check_semantic_cache(user_query, user_role)

    if cached_ans:
        elapsed = time.perf_counter() - start_time
        return {
            "source": f"REDIS_SEMANTIC_CACHE (Similarity: {sim_score:.3f})",
            "model_used": "NONE (0 LLM Tokens)",
            "latency_sec": round(elapsed, 3),
            "response": cached_ans,
        }

    target_model = classify_and_route(user_query) if not is_followup else FRONTIER_LOCAL_MODEL

    if target_model == SMALL_FAST_MODEL:
        sys_prompt = prompt_mod.load_system_prompt()
        res = ollama.chat(
            model=SMALL_FAST_MODEL,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": user_query},
            ],
        )
        answer = res["message"]["content"]
    else:
        answer = mcp_mod.run_agent_turn(user_query, session_jwt=session_jwt, chat_history=chat_history)

    if not is_followup and query_vec is not None:
        store_in_semantic_cache(user_query, query_vec, answer, user_role)

    elapsed = time.perf_counter() - start_time
    return {
        "source": "LIVE_INFERENCE (Multi-Turn)" if is_followup else "LIVE_INFERENCE (Cache Miss)",
        "model_used": target_model,
        "latency_sec": round(elapsed, 3),
        "response": answer,
    }