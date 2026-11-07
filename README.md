# Laggy — Local AI "Second Brain"

Laggy is a secure, private, air-gapped local AI assistant running strictly on `127.0.0.1`. It features multi-turn memory, Role-Based Access Control (RBAC), Microsoft Presidio PII redaction, Llama-Guard safety filters, local vector search via Qdrant, anonymous web searching via DuckDuckGo, and an automated end-to-end testing suite.

---

## Prerequisites & System Requirements

Before you begin, ensure your system meets the requirements to run local LLMs and vector databases:
- **Operating System**: Windows 10/11, macOS, or Linux.
- **Python**: Version 3.10 or higher.
- **Docker**: Required to run Qdrant and Redis locally.
- **Hardware Configuration**: 
  - **GPU Recommended**: An NVIDIA GPU with at least 8GB to 12GB of VRAM (e.g., RTX 3070 or better) ensures fast, real-time local inference.
  - **RAM**: At least 16GB of system RAM (32GB recommended if running models entirely on CPU).

---

## Step 1: Install & Configure Ollama

Ollama is the local inference engine that powers your LLMs and embedding models.

1. Download and install Ollama from [ollama.com](https://ollama.com).
2. Open your terminal (or Command Prompt / PowerShell) and pull the required models based on your hardware configuration:

### Model Selection Guide Based on PC Configuration:
* **High-End PC (NVIDIA GPU with 12GB+ VRAM / 32GB RAM):**
  ```bash
  ollama pull qwen2.5:7b
  ollama pull qwen2.5:3b
  ollama pull bge-m3
  ollama pull llama-guard3:1b
  ```
* **Mid-Range / Budget PC (8GB VRAM or CPU-only with 16GB RAM):**
  If your VRAM is limited, you can rely on the 3B model for fast reasoning:
  ```bash
  ollama pull qwen2.5:3b
  ollama pull bge-m3
  ollama pull llama-guard3:1b
  ```

3. Verify Ollama is running locally by typing:
   ```bash
   ollama list
   ```

---

## Step 2: Install & Start Infrastructure (Docker)

Laggy relies on **Qdrant** for vector search and **Redis** for semantic caching. 

1. **Start Qdrant Vector Database** (Port `6333`):
   ```bash
   docker run -d -p 6333:6333 -p 6334:6334 qdrant/qdrant
   ```
2. **Start Redis Cache** (Port `6379`):
   ```bash
   docker run -d -p 6379:6379 redis:alpine
   ```

---

## Step 3: Clone Repository & Install Dependencies

1. Clone or download your project folder to your local machine, then navigate into the directory:
   ```bash
   cd local-second-brain
   ```

2. Install the required Python packages:
   ```bash
   pip install -r requirements.txt
   ```

---

## Step 4: Set Up Local Data Folders

Create the required local data structure inside your project directory to store documents and databases:

```text
local-second-brain/
└── data/
    ├── standard_docs/   # Drop general notes or text files here
    └── vault_docs/      # Drop highly confidential documents here (requires 2FA)
```

1. Create the directories:
   ```bash
   mkdir data\standard_docs
   mkdir data\vault_docs
   ```
2. Place any sample `.txt`, `.md`, or `.pdf` files you want Laggy to index inside `data/standard_docs/`.

---

## Step 5: Get the Server Running

Once all prerequisites, dependencies, and data folders are in place, you can start your application.

1. **Start the FastAPI backend server**:
   ```bash
   python src/main.py
   ```
   *(Alternatively, if you are on Windows, you can double-click **`run_tests.bat`** to automatically boot the server, execute diagnostics, and log everything).*

2. **Access the Web UI**:
   Open your browser and navigate to:
   **`http://127.0.0.1:8000`**

Laggy is now fully up and running locally, air-gapped, and ready for use!
