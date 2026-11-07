import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PROFILE_PATH = BASE_DIR / "data" / "profile.json"


def load_system_prompt(retrieved_context: str = "") -> str:
    """Builds a hybrid system prompt: strict on personal facts, open for brainstorming."""
    if PROFILE_PATH.exists():
        profile = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    else:
        profile = {"assistant_name": "Laggy", "owner_name": "Hrishikesh", "preferences": []}

    prefs = "\n".join(f"- {p}" for p in profile.get("preferences", []))

    context_block = (
        retrieved_context
        if retrieved_context and "No relevant local files" not in retrieved_context
        else "None."
    )

    prompt = f"""You are {profile.get('assistant_name')}, the local, air-gapped personal AI assistant and thought partner for {profile.get('owner_name')} ({profile.get('role', '')}).

CORE OPERATING MODES:
{prefs}
1. PERSONAL FACT LOOKUPS: When asked for specific personal facts (dates, PINs, contracts, where the user lived), rely strictly on the Indexed Document Excerpts below and cite the filename in brackets (e.g., [basic_info.txt]). If a specific personal fact is missing, state: "I do not see that in your indexed files."
2. BRAINSTORMING, ADVICE & MODIFICATIONS: When the user asks for suggestions, ideas, code, workout modifications, or "what do you think", DO NOT say "I do not see that in your indexed files." Instead, think out loud using your general AI knowledge while cross-referencing the user's personal constraints from the Indexed Document Excerpts (for example, respecting their dietary exclusions or spinal loading limits in [fitness_and_diet.txt]).

Indexed Document Excerpts:
{context_block}
"""
    return prompt