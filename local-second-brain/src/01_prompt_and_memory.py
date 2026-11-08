import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_PATH = BASE_DIR / "data" / "profile.json"
SKILLS_DIR = BASE_DIR / "data" / "skills"

def get_all_skills() -> list:
    skills = []
    if not SKILLS_DIR.exists(): return skills
    for skill_path in SKILLS_DIR.rglob("SKILL.md"):
        try:
            content = skill_path.read_text(encoding="utf-8")
            match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
            name = skill_path.parent.name
            if match:
                name_m = re.search(r"name:\s*(.+)", match.group(1))
                if name_m: name = name_m.group(1).strip()
            skills.append({"id": skill_path.parent.name, "name": name})
        except Exception:
            continue
    return skills

def get_skill_summaries() -> str:
    if not SKILLS_DIR.exists(): return "None learned yet."
    summaries = []
    for skill_path in SKILLS_DIR.rglob("SKILL.md"):
        try:
            content = skill_path.read_text(encoding="utf-8")
            match = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
            if match:
                name_m = re.search(r"name:\s*(.+)", match.group(1))
                desc_m = re.search(r"description:\s*(.+)", match.group(1))
                if name_m and desc_m:
                    summaries.append(f"- {name_m.group(1).strip()}: {desc_m.group(1).strip()}")
        except Exception:
            continue
    return "\n".join(summaries) if summaries else "None learned yet."

def load_system_prompt(retrieved_context: str = "", memory_context: str = "", active_skill: str = None) -> str:
    if PROFILE_PATH.exists():
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    else:
        profile = {"assistant_name": "Laggy", "owner_name": "Hrishikesh", "preferences": []}

    prefs = "\n".join(f"- {p}" for p in profile.get("preferences", []))

    skill_block_text = ""
    if active_skill and (SKILLS_DIR / active_skill / "SKILL.md").exists():
        skill_content = (SKILLS_DIR / active_skill / "SKILL.md").read_text(encoding="utf-8")
        skill_block_text = f"USER EXPLICITLY SELECTED THIS SKILL TO BE ACTIVE FOR THIS TURN:\n\n{skill_content}\n\nFOLLOW THESE INSTRUCTIONS STRICTLY."
    else:
        summaries = get_skill_summaries()
        skill_block_text = f"ACTIVE CUSTOM SKILLS (Summaries Only):\n{summaries}"

    file_block = retrieved_context if retrieved_context and "No relevant local files" not in retrieved_context else "None."
    memory_block = memory_context if memory_context else "No relevant previous conversations."

    prompt = f"""You are {profile.get('assistant_name')}, a highly capable AI assistant for {profile.get('owner_name')}.

CORE OPERATING MODES:
{prefs}
1. USE YOUR TOOLS: You have access to backend functions (Search Web, Search Qdrant, Save File, Terminal). Use them seamlessly to fulfill user requests. Do not ask for permission—just act.
2. FILE SAVING: If asked to save, write, or export a file, immediately call `save_file_to_disk`. All files go to `local-second-brain/data/output_notes/`.
3. TERMINAL COMMANDS: If asked to run a command or check the disk, immediately call `execute_terminal_command`.
4. PROGRESSIVE SKILL DISCLOSURE (agentskills.io): If a user's request matches a custom skill below, FIRST call `read_skill_details` to load it.
5. CONTINUITY: Reference the "Relevant Past Conversations" block to maintain context.

{skill_block_text}

Relevant Past Conversations (Cross-Chat Memory):
{memory_block}

Indexed Document Excerpts:
{file_block}
"""
    return prompt