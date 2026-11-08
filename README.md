# Laggy — Local AI "Second Brain"

Laggy is a highly optimized, secure, private, **air-gapped** personal AI assistant designed to run locally on an 8GB VRAM GPU (e.g., RTX 3070) and bound strictly to `127.0.0.1`.

It features progressive skill disclosure (Claude-style [agentskills.io](https://agentskills.io) standard), human-in-the-loop terminal execution, Qdrant-backed RAG for personal files, Redis-backed cross-chat semantic memory, multi-turn memory, Role-Based Access Control (RBAC), Microsoft Presidio PII redaction, Llama-Guard safety filters, anonymous web searching via DuckDuckGo Lite, a LiquidGlass browser UI, and an automated end-to-end testing suite.

> **Privacy note:** Laggy is designed to be fully local/offline. The `search_the_web` tool is the only feature that needs outbound network access — skip it if you want a strictly air-gapped setup.

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Prerequisites & System Requirements](#prerequisites--system-requirements)
3. [Step 1: Install & Configure Ollama](#step-1-install--configure-ollama)
4. [Step 2: Start Infrastructure (Docker)](#step-2-start-infrastructure-docker)
5. [Step 3: Clone Repository & Install Dependencies](#step-3-clone-repository--install-dependencies)
6. [Step 4: Set Up Local Data Folders](#step-4-set-up-local-data-folders)
7. [Step 5: Run Laggy](#step-5-run-laggy)
8. [Role-Based Access Control (RBAC) & Tools](#role-based-access-control-rbac--tools)
9. [Progressive Skill Disclosure](#progressive-skill-disclosure)
10. [Automated Testing](#automated-testing)

---

## System Architecture

| Layer | Technology |
| --- | --- |
| **Frontend** | HTML/JS with LiquidGlass UI (browser-based, native text selection, chat export) |
| **Backend** | FastAPI, Uvicorn, SQLite (session / chat history) |
| **Vector DB** | Qdrant — personal knowledge vault / RAG (port `6333`) |
| **Semantic Cache** | Redis — cross-chat memory (port `6379`) |
| **LLM Engine** | Ollama — Qwen 2.5 7B (`qwen2.5:3b` on lower-VRAM machines) |
| **Embeddings** | Nomic Embed Text (`nomic-embed-text`); `bge-m3` as an alternative |
| **Guardrails** | Llama Guard 3 1B (`llama-guard3:1b`) + Microsoft Presidio PII redaction |
| **Web Search** | DuckDuckGo Lite (anonymous, optional) |
| **Local Storage** | `data/` folders + `data/profile.json` |
| **Testing** | Automated end-to-end diagnostics suite (`run_tests.bat` on Windows) |

---

## Prerequisites & System Requirements

- **Operating System**: Windows 10/11, macOS, or Linux.
- **Python**: 3.10 or higher.
- **Ollama**: installed and running in the background.
- **Redis Server**: running locally on port `6379`.
- **Qdrant Server**: running locally on port `6333`.
- **Docker**: recommended (used below) to run Qdrant and Redis locally; native installs work too as long as the ports above are available.
- **Hardware Configuration**:
  - **GPU Recommended**: An NVIDIA GPU with at least 8GB–12GB of VRAM (e.g., RTX 3070 or better) for fast, real-time local inference.
  - **RAM**: At least 16GB of system RAM (32GB recommended if running models entirely on CPU).

---

## Step 1: Install & Configure Ollama

Ollama is the local inference engine powering the LLM, embedding, and guardrail models.

1. Download and install Ollama from [ollama.com](https://ollama.com).
2. **CRITICAL:** Pull the required inference, embedding, and guardrail models before starting the server.

### Default stack (8GB VRAM, e.g. RTX 3070)

```bash
# Main reasoning and tool-calling agent
ollama pull qwen2.5:7b

# Fast, lightweight embedding model for Qdrant RAG and Redis memory
ollama pull nomic-embed-text

# Input/Output security guardrail model
ollama pull llama-guard3:1b
```

### Model Selection Guide Based on PC Configuration

- **High-End PC (NVIDIA GPU with 12GB+ VRAM / 32GB RAM):**

  ```bash
  ollama pull qwen2.5:7b
  ollama pull qwen2.5:3b
  ollama pull bge-m3
  ollama pull llama-guard3:1b
  ```

- **Mid-Range / Budget PC (8GB VRAM or CPU-only with 16GB RAM):**
  If VRAM is limited, rely on the 3B model for fast reasoning:

  ```bash
  ollama pull qwen2.5:3b
  ollama pull bge-m3
  ollama pull llama-guard3:1b
  ```

> **Note:** Use **one** embedding model consistently (`nomic-embed-text` is the default above; `bge-m3` is the alternative). Their vector dimensions differ, so switching models requires re-creating / re-indexing the Qdrant collection.

3. Verify Ollama is running locally:

   ```bash
   ollama list
   ```

---

## Step 2: Start Infrastructure (Docker)

Laggy relies on **Qdrant** for vector search / RAG and **Redis** for semantic caching and cross-chat memory.

1. **Start Qdrant Vector Database** (ports `6333` / `6334`):

   ```bash
   docker run -d -p 6333:6333 -p 6334:6334 qdrant/qdrant
   ```

2. **Start Redis Cache** (port `6379`):

   ```bash
   docker run -d -p 6379:6379 redis:alpine
   ```

---

## Step 3: Clone Repository & Install Dependencies

1. Clone the repository and enter the project directory:

   ```bash
   git clone https://github.com/yourusername/Laggy---Local-AI-Second-Brain.git
   cd local-second-brain
   ```

   *(Alternatively, download the project folder and `cd local-second-brain`.)*

2. Install the Python dependencies — either via the requirements file:

   ```bash
   pip install -r requirements.txt
   ```

   ...or directly:

   ```bash
   pip install fastapi uvicorn pydantic ollama pypdf qdrant-client redis pyotp pyjwt watchdog presidio-analyzer presidio-anonymizer
   ```

---

## Step 4: Set Up Local Data Folders

Create the required directory structure inside the project root:

```text
local-second-brain/
├── data/
│   ├── output_notes/     # Where the AI saves files via tools
│   ├── skills/           # Custom Claude-style SKILL.md folders
│   ├── standard_docs/    # Standard RAG ingestion folder
│   ├── vault_docs/       # 2FA-protected RAG ingestion folder
│   └── profile.json      # Assistant / owner preferences
├── src/
│   ├── main.py
│   └── ...
├── requirements.txt
└── run_tests.bat         # Windows: boot + diagnostics + logs
```

Create the directories:

```bash
# macOS / Linux
mkdir -p data/output_notes data/skills data/standard_docs data/vault_docs
```

```bat
:: Windows (Command Prompt)
mkdir data\output_notes
mkdir data\skills
mkdir data\standard_docs
mkdir data\vault_docs
```

Then:

1. Drop general `.txt`, `.md`, or `.pdf` notes you want indexed into `data/standard_docs/`.
2. Put highly confidential documents into `data/vault_docs/` (requires 2FA to query).
3. Place custom skill folders (each containing a `SKILL.md`) in `data/skills/`.
4. Files written by the AI land in `data/output_notes/`.
5. Create `data/profile.json` with your preferences:

   ```json
   {
     "assistant_name": "Laggy",
     "owner_name": "Your Name",
     "role": "Your Role",
     "preferences": [
       "Keep answers concise and direct.",
       "Format code snippets with proper markdown.",
       "Never use emojis in professional responses."
     ]
   }
   ```

---

## Step 5: Run Laggy

Once prerequisites, dependencies, Ollama models, and data folders are in place, start the FastAPI backend:

```bash
# From the project root
python src/main.py

# Or equivalently
cd src
python main.py
```

*(On Windows, you can also double-click **`run_tests.bat`** to automatically boot the server, execute diagnostics, and log everything.)*

Open your browser and navigate to **`http://127.0.0.1:8000/`**.

Laggy is now fully up and running locally, air-gapped, and ready to use.

---

## Role-Based Access Control (RBAC) & Tools

Laggy uses a **Zero-Trust architecture**. By default, you are a standard user. To execute local files, save notes to your hard drive, or permanently teach the AI new skills, you must elevate your session to `vault_admin`:

1. Click **"Unlock 2FA Vault"** in the UI.
2. Enter your 6-digit TOTP code (or type `UNLOCK` for local testing).

### Available Tools (Vault Admin)

| Tool | Description |
| --- | --- |
| `search_knowledge_base` | Queries Qdrant for personal files (RAG). |
| `search_the_web` | Fetches live data via DuckDuckGo Lite. |
| `save_file_to_disk` | Writes a `.txt` or `.md` file to `data/output_notes/`. |
| `execute_terminal_command` | Runs local shell commands (human-in-the-loop approval required). |
| `learn_new_skill` | Dynamically creates a `SKILL.md` file in `data/skills/`. |
| `read_skill_details` | Fetches full skill instructions via progressive disclosure. |

---

## Progressive Skill Disclosure

Skills follow the Claude-style [agentskills.io](https://agentskills.io) standard: each skill lives in its own folder under `data/skills/` with a `SKILL.md` file. Only lightweight skill metadata is loaded into the model context up front; full instructions are pulled on demand with `read_skill_details`, keeping the context window small on 8GB VRAM hardware. New skills can be created permanently at runtime with `learn_new_skill` (requires `vault_admin`).

---

## Automated Testing

An automated end-to-end testing suite ships with the project. On Windows, double-click **`run_tests.bat`** to boot the server, run diagnostics against the API, tools, guardrails, and RAG pipeline, and write everything to a log file.
