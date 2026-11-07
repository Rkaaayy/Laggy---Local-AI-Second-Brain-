import sqlite3
from pathlib import Path
from importlib import import_module
import ollama

# Compatible with both MCP 2.x (MCPServer) and MCP 1.x (FastMCP)
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

# Load our previous modules
BASE_DIR = Path(__file__).resolve().parent.parent
prompt_mod = import_module("01_prompt_and_memory")
rag_mod = import_module("02_rag_ingestion")
auth_mod = import_module("04_auth_rbac")

# Initialize Official MCP 2.x Server
mcp = MCPServer("LaggyPersonalMCP")

TASKS_DB_PATH = BASE_DIR / "data" / "personal_tasks.db"
NOTES_OUTPUT_DIR = BASE_DIR / "data" / "output_notes"


def init_tasks_db():
    """Creates a local SQLite database for the agent's task management tool."""
    TASKS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(TASKS_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task TEXT NOT NULL,
                status TEXT DEFAULT 'pending'
            )
            """
        )


# =====================================================================
# MCP TOOLS (Each tool enforces RBAC via the passed session JWT token)
# =====================================================================

@mcp.tool()
def search_knowledge_base(query: str, auth_token: str) -> str:
    """
    Searches local personal documents in Qdrant.
    Automatically filters results based on the user's JWT clearance level.
    """
    session = auth_mod.verify_token_and_permissions(auth_token, required_tool="search_knowledge_base")
    allowed = session["allowed_clearances"]

    # Try standard vector search first
    context = rag_mod.retrieve_context(query, allowed_clearances=allowed, top_k=3)
    
    # Fallback: if nothing found with the specific query, fetch general recent file chunks
    if not context or "No relevant local files" in context:
        print(f"  [RAG Fallback] Specific query '{query}' yielded no hits. Broadening search...")
        context = rag_mod.retrieve_context("notes roadmap project contract", allowed_clearances=allowed, top_k=5)

    if not context:
        return f"No documents found matching '{query}' for clearance level: {allowed}."
    return context


@mcp.tool()
def manage_todo_tasks(action: str, task_description: str = "", auth_token: str = "") -> str:
    """
    Manages the user's local SQLite to-do list.
    action must be either 'add' or 'list'.
    """
    auth_mod.verify_token_and_permissions(auth_token, required_tool="manage_todo_tasks")
    init_tasks_db()

    with sqlite3.connect(TASKS_DB_PATH) as conn:
        if action.lower() == "add" and task_description:
            conn.execute("INSERT INTO tasks (task) VALUES (?)", (task_description,))
            conn.commit()
            return f"Successfully added task to local SQLite DB: '{task_description}'"

        rows = conn.execute("SELECT id, task, status FROM tasks").fetchall()
        if not rows:
            return "Your local task list is currently empty."
        return "Current Tasks:\n" + "\n".join(f"- [{r[2].upper()}] #{r[0]}: {r[1]}" for r in rows)


@mcp.tool()
def save_markdown_note(filename: str, content: str, auth_token: str = "") -> str:
    """
    Saves a Markdown (.md) note to the local disk.
    RESTRICTED: Requires 'vault_admin' RBAC role.
    """
    auth_mod.verify_token_and_permissions(auth_token, required_tool="save_markdown_note")
    NOTES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Sanitize filename to prevent directory traversal
    safe_name = Path(filename).name
    if not safe_name.endswith(".md"):
        safe_name += ".md"

    target_path = NOTES_OUTPUT_DIR / safe_name
    target_path.write_text(content, encoding="utf-8")
    return f"Successfully wrote note to {target_path}"


# =====================================================================
# AGENT TOOL-CALLING LOOP (Connects Qwen 2.5 7B to the MCP Tools)
# =====================================================================


@mcp.tool()
def search_the_web(query: str, auth_token: str) -> str:
    """
    Searches the public internet using an anonymous search endpoint.
    """
    auth_mod.verify_token_and_permissions(auth_token, required_tool="search_the_web")
    try:
        import urllib.request
        import urllib.parse
        import re
        
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(
            url, 
            data=None, 
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
        )
        
        html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
        
        results = []
        # Updated, more resilient regex for DuckDuckGo Lite
        blocks = re.findall(r'<a class="result__url" href="([^"]+)".*?>(.*?)</a>.*?<a class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL | re.IGNORECASE)
        
        for url, title, snippet in blocks[:4]:
            clean_title = re.sub(r'<[^>]+>', '', title).strip()
            clean_snippet = re.sub(r'<[^>]+>', '', snippet).strip()
            clean_url = urllib.parse.unquote(url.split('uddg=')[-1].split('&')[0]) if 'uddg=' in url else url
            results.append(f"Title: {clean_title}\nURL: {clean_url}\nSummary: {clean_snippet}")
            
        # Hard Fallback: If DDG changed their HTML again, strip all HTML and return the raw text block
        if not results:
            text_only = re.sub(r'<style.*?</style>', '', html, flags=re.DOTALL)
            text_only = re.sub(r'<script.*?</script>', '', text_only, flags=re.DOTALL)
            text_only = re.sub(r'<[^>]+>', ' ', text_only)
            text_only = re.sub(r'\s+', ' ', text_only).strip()
            # Grab a chunk of text that likely contains the results
            fallback_text = text_only[1000:4000] if len(text_only) > 1000 else text_only
            return f"Raw Web Content Fallback:\n{fallback_text}"
            
        return "\n\n---\n\n".join(results)
    except Exception as e:
        return f"Web search failed: {e}"

OLLAMA_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_the_web",
            "description": "Search the public internet for external information, papers, or news. Do not use for personal files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Search the user's indexed personal files, notes, workout plans, or employment contracts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query to look up in Qdrant."}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_todo_tasks",
            "description": "Add a new task to the user's to-do list or list existing tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "list"], "description": "'add' or 'list'"},
                    "task_description": {"type": "string", "description": "The task text when action is 'add'."},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_markdown_note",
            "description": "Save a summary or note as a Markdown file on disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string", "description": "Name of the markdown file, e.g. summary.md"},
                    "content": {"type": "string", "description": "Text content to write into the file."},
                },
                "required": ["filename", "content"],
            },
        },
    },
]

TOOL_DISPATCH = {
    "search_the_web": search_the_web,
    "search_knowledge_base": search_knowledge_base,
    "manage_todo_tasks": manage_todo_tasks,
    "save_markdown_note": save_markdown_note,
}


def run_agent_turn(user_query: str, session_jwt: str, chat_history: list = None, max_steps: int = 4) -> str:
    """
    Combines recent conversation context for RAG retrieval, injects multi-turn
    chat history, and runs the MCP tool loop.
    """
    session = auth_mod.verify_token_and_permissions(session_jwt)
    allowed_clearances = session["allowed_clearances"]

    # Build a context-aware search query using the last user message + current query
    rag_search_query = user_query
    if chat_history:
        recent_user_msgs = [m["content"] for m in chat_history if m.get("role") == "user"]
        if recent_user_msgs:
            rag_search_query = f"{recent_user_msgs[-1]} {user_query}"

    initial_rag_context = rag_mod.retrieve_context(
        rag_search_query, allowed_clearances=allowed_clearances, top_k=4
    )
    sys_prompt = prompt_mod.load_system_prompt(initial_rag_context)

    messages = [{"role": "system", "content": sys_prompt}]

    # Keep the last 6 messages (3 turns) so follow-ups like "change this" work naturally
    if chat_history:
        messages.extend(chat_history[-6:])

    messages.append({"role": "user", "content": user_query})

    for step in range(max_steps):
        response = ollama.chat(
            model="qwen2.5:7b",
            messages=messages,
            tools=OLLAMA_TOOL_SCHEMAS,
        )
        msg = response["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            return msg["content"]

        for call in tool_calls:
            fn_name = call["function"]["name"]
            fn_args = call["function"]["arguments"]
            print(f"  [Step {step+1} MCP Tool Call] -> {fn_name}({fn_args})")

            tool_fn = TOOL_DISPATCH.get(fn_name)
            try:
                tool_output = tool_fn(**fn_args, auth_token=session_jwt)
            except PermissionError as e:
                tool_output = str(e)
            except Exception as e:
                tool_output = f"Tool execution error: {e}"

            print(f"  [Step {step+1} MCP Tool Result] -> {str(tool_output)[:140]}...")
            messages.append({"role": "tool", "content": str(tool_output)})

    final_response = ollama.chat(model="qwen2.5:7b", messages=messages)
    return final_response["message"]["content"]