import sqlite3
import subprocess
import json
from pathlib import Path
from importlib import import_module
import ollama

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

BASE_DIR = Path(__file__).resolve().parent.parent
prompt_mod = import_module("01_prompt_and_memory")
rag_mod = import_module("02_rag_ingestion")
auth_mod = import_module("04_auth_rbac")

mcp = MCPServer("LaggyPersonalMCP")

TASKS_DB_PATH = BASE_DIR / "data" / "personal_tasks.db"
NOTES_OUTPUT_DIR = BASE_DIR / "data" / "output_notes"
SKILLS_DIR = BASE_DIR / "data" / "skills"

def init_tasks_db():
    TASKS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(TASKS_DB_PATH) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, task TEXT NOT NULL, status TEXT DEFAULT 'pending')")

@mcp.tool()
def search_knowledge_base(query: str, auth_token: str) -> str:
    session = auth_mod.verify_token_and_permissions(auth_token, required_tool="search_knowledge_base")
    allowed = session["allowed_clearances"]
    context = rag_mod.retrieve_context(query, allowed_clearances=allowed, top_k=3)
    if not context or "No relevant local files" in context:
        context = rag_mod.retrieve_context("notes roadmap project contract", allowed_clearances=allowed, top_k=5)
    if not context:
        return f"No documents found matching '{query}' for clearance level: {allowed}."
    return context

@mcp.tool()
def manage_todo_tasks(action: str, task_description: str = "", auth_token: str = "") -> str:
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
def save_file_to_disk(filename: str, content: str, auth_token: str = "") -> str:
    auth_mod.verify_token_and_permissions(auth_token, required_tool="save_file_to_disk")
    NOTES_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    if not (safe_name.endswith(".md") or safe_name.endswith(".txt")):
        safe_name += ".txt"
    target_path = NOTES_OUTPUT_DIR / safe_name
    target_path.write_text(content, encoding="utf-8")
    return f"SUCCESS: Wrote file to absolute path: {target_path.absolute()}"

@mcp.tool()
def execute_terminal_command(command: str, auth_token: str = "") -> str:
    auth_mod.verify_token_and_permissions(auth_token, required_tool="execute_terminal_command")
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=15)
        output = result.stdout if result.stdout else result.stderr
        if not output:
            return "Command executed successfully with no output."
        return output[:2000]
    except subprocess.TimeoutExpired:
        return "Error: Command execution timed out after 15 seconds."
    except Exception as e:
        return f"Error executing command: {e}"

@mcp.tool()
def learn_new_skill(skill_name: str, description: str, instructions: str, auth_token: str = "") -> str:
    auth_mod.verify_token_and_permissions(auth_token, required_tool="learn_new_skill")
    safe_name = "".join(c for c in skill_name if c.isalnum() or c in ("_", "-")).strip()
    skill_dir = SKILLS_DIR / safe_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    md_content = f"---\nname: {skill_name}\ndescription: {description}\n---\n\n{instructions}"
    (skill_dir / "SKILL.md").write_text(md_content, encoding="utf-8")
    return f"SUCCESS: Skill '{skill_name}' created as a standard SKILL.md inside {skill_dir}. It will auto-load on the next turn."

@mcp.tool()
def read_skill_details(skill_name: str, auth_token: str = "") -> str:
    auth_mod.verify_token_and_permissions(auth_token, required_tool="read_skill_details")
    safe_name = "".join(c for c in skill_name if c.isalnum() or c in ("_", "-")).strip()
    skill_dir = SKILLS_DIR / safe_name
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return f"Error: Skill '{skill_name}' (folder {safe_name}) not found on disk."
    content = skill_md.read_text(encoding="utf-8")
    other_files = [f.name for f in skill_dir.iterdir() if f.is_file() and f.name != "SKILL.md"]
    aux_info = f"\n\n[System Note: The following auxiliary files are available in this skill's folder: {', '.join(other_files)}]" if other_files else ""
    return f"FULL SKILL INSTRUCTIONS:\n\n{content}{aux_info}"

@mcp.tool()
def search_the_web(query: str, auth_token: str) -> str:
    auth_mod.verify_token_and_permissions(auth_token, required_tool="search_the_web")
    try:
        import urllib.request
        import urllib.parse
        import re
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(url, data=None, headers={'User-Agent': 'Mozilla/5.0'})
        html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8')
        results = []
        blocks = re.findall(r'<a class="result__url" href="([^"]+)".*?>(.*?)</a>.*?<a class="result__snippet[^>]*>(.*?)</a>', html, re.DOTALL | re.IGNORECASE)
        for url, title, snippet in blocks[:4]:
            clean_title = re.sub(r'<[^>]+>', '', title).strip()
            clean_snippet = re.sub(r'<[^>]+>', '', snippet).strip()
            clean_url = urllib.parse.unquote(url.split('uddg=')[-1].split('&')[0]) if 'uddg=' in url else url
            results.append(f"Title: {clean_title}\nURL: {clean_url}\nSummary: {clean_snippet}")
        if not results:
            return "No web results found."
        return "\n\n---\n\n".join(results)
    except Exception as e:
        return f"Web search failed: {e}"

OLLAMA_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_the_web",
            "description": "Search the internet for external information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "Search the user's indexed files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_todo_tasks",
            "description": "Add a task or list existing tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "list"]},
                    "task_description": {"type": "string"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_file_to_disk",
            "description": "Save text or plans to a file on the hard drive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content": {"type": "string"}
                },
                "required": ["filename", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_terminal_command",
            "description": "Execute a shell command on the user's local machine.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"}
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "learn_new_skill",
            "description": "Save a new behavioral skill or workflow instruction.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string"},
                    "description": {"type": "string"},
                    "instructions": {"type": "string"}
                },
                "required": ["skill_name", "description", "instructions"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_skill_details",
            "description": "Fetch the full instructions for an active skill.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string"}
                },
                "required": ["skill_name"]
            }
        }
    }
]

TOOL_DISPATCH = {
    "search_the_web": search_the_web, "search_knowledge_base": search_knowledge_base, 
    "manage_todo_tasks": manage_todo_tasks, "save_file_to_disk": save_file_to_disk, 
    "execute_terminal_command": execute_terminal_command, "learn_new_skill": learn_new_skill,
    "read_skill_details": read_skill_details
}

def run_agent_turn(user_query: str, session_jwt: str, chat_history: list = None, max_steps: int = 4, memory_context: str = "", active_skill: str = None) -> str:
    session = auth_mod.verify_token_and_permissions(session_jwt)
    allowed_clearances = session["allowed_clearances"]

    rag_search_query = user_query
    if chat_history:
        recent_user_msgs = [m["content"] for m in chat_history if m.get("role") == "user"]
        if recent_user_msgs:
            rag_search_query = f"{recent_user_msgs[-1]} {user_query}"

    initial_rag_context = rag_mod.retrieve_context(rag_search_query, allowed_clearances=allowed_clearances, top_k=4)
    sys_prompt = prompt_mod.load_system_prompt(retrieved_context=initial_rag_context, memory_context=memory_context, active_skill=active_skill)

    messages = [{"role": "system", "content": sys_prompt}]
    
    if chat_history: 
        messages.extend(chat_history)
        
    messages.append({"role": "user", "content": user_query})

    for step in range(max_steps):
        response = ollama.chat(model="qwen2.5:7b", messages=messages, tools=OLLAMA_TOOL_SCHEMAS)
        msg = response["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls: return msg["content"]

        for call in tool_calls:
            fn_name = call["function"]["name"]
            fn_args = call["function"]["arguments"]
            print(f"  [Step {step+1} MCP Tool Call] -> {fn_name}({fn_args})")

            try:
                tool_output = TOOL_DISPATCH.get(fn_name)(**fn_args, auth_token=session_jwt)
            except Exception as e:
                tool_output = str(e)
            
            messages.append({"role": "tool", "content": str(tool_output)})

    final_response = ollama.chat(model="qwen2.5:7b", messages=messages)
    return final_response["message"]["content"]