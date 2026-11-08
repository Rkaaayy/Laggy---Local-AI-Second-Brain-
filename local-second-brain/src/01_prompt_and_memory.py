import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_PATH = BASE_DIR / "data" / "profile.json"


def load_system_prompt(retrieved_context: str = "", memory_context: str = "") -> str:
    """Builds a hybrid system prompt including personal files and cross-chat memory."""
    if PROFILE_PATH.exists():
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    else:
        profile = {"assistant_name": "Laggy", "owner_name": "Hrishikesh", "preferences": []}

    prefs = "\n".join(f"- {p}" for p in profile.get("preferences", []))

    file_block = (
        retrieved_context
        if retrieved_context and "No relevant local files" not in retrieved_context
        else "None."
    )
    
    memory_block = memory_context if memory_context else "No relevant previous conversations."

    prompt = f"""You are {profile.get('assistant_name')}, the local, air-gapped personal AI assistant and thought partner for {profile.get('owner_name')} ({profile.get('role', '')}).

CORE OPERATING MODES:
{prefs}
1. PERSONAL FACT LOOKUPS: When asked for specific personal facts (dates, PINs, contracts), rely strictly on the Indexed Document Excerpts below and cite the filename in brackets. If a specific personal fact is missing, state: "I do not see that in your indexed files."
2. CONTINUITY & CONTEXT: Use the "Relevant Past Conversations" block to maintain cross-chat memory. If the user mentions something related to a past chat (e.g., adding a recipe to a previously discussed meal plan), integrate the two seamlessly.
3. BRAINSTORMING, ADVICE & MODIFICATIONS: When brainstorming, think out loud using your general AI knowledge while cross-referencing constraints from the Indexed Documents.

Relevant Past Conversations (Cross-Chat Memory):
{memory_block}

Indexed Document Excerpts:
{file_block}
"""
    return prompt