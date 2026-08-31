# Agentic Lead Gateway — Project Documentation

## 1. Problem, user, and value

This project was built for lead-generation agencies, real-estate wholesalers, and service businesses that need to send inbound leads to many different client CRMs without writing custom code for each integration.

The bottleneck today is that every new CRM partner usually requires a developer to manually map raw lead fields to a destination schema, write custom HTTP payloads, and manage token/auth setup. This slows onboarding, creates fragile integrations, and often forces clients to share sensitive credentials.

The Agentic Lead Gateway solves this by doing three things:

- receiving messy lead JSON from a single lead key
- using a local LLM to normalize and map fields semantically
- dispatching to the proper CRM endpoint with validation, retry logic, and human review if needed

This turns a multi-day integration sprint into a fast, UI-driven route setup process that can be repeated safely across many destinations.

---

## 2. Overview of the solution

The gateway combines a FastAPI web app, SQLite persistence, and local Ollama-based agent logic to create a safe ingestion and dispatch system for CRM leads.

### Core capabilities

- Semantic field mapping using local LLM inference
- Web-document search for unknown CRM onboarding and endpoint discovery
- In-memory and SQLite-based route and memory persistence
- Retry loops with API error correction
- Placeholder URL and fake endpoint blocking
- Human-in-the-loop quarantine for unrecoverable errors
- Live trajectory logging for observability and debugging

### Project structure

- `main.py` — FastAPI application and dashboard endpoints
- `agent.py` — semantic mapping of messy payloads to target schema
- `agent_helper.py` — CRM onboarding instructions and schema discovery
- `dispatch.py` — request formulation, retry loop, endpoint enforcement, and HTTP dispatch logic
- `db.py` — SQLite route, memory, and quarantine database
- `evaluate.py` — baseline vs agent evaluation suite
- `test_dispatch.py` — regression tests for payload and endpoint safety
- `static/index.html` — dashboard UI
- `trajectories/` — saved trajectory runs and representative examples
- `.env.example` — environment configuration template

---

## 3. Agent instructions and behavioral logic

The system uses two main autonomous agents:

### A. Onboarding / CRM guidance agent

This agent is responsible for explaining how a user can access a CRM token or inbound endpoint and for inferring the common field names that a CRM expects.

It is primarily defined in `agent_helper.py` and uses a system/user prompt similar to:

```text
You are an expert CRM Integration Engineer. The user wants to integrate '{crm_name}'.

Using live search results above (if helpful) or your internal knowledge, provide concise, numbered, UI-specific steps on how to find the API Access Token, Private App Token, or Inbound Webhook URL in {crm_name}.
Also, carefully review the search results to infer a list of standard string field names this CRM uses for a real estate lead or contact.

Return ONLY valid JSON matching this exact schema:
{
  "instructions": "1. Step one...\n2. Step two...",
  "expected_fields": ["first_name", "last_name", "phone", "email", "address", "notes"]
}
```

This agent is intentionally constrained to output structured JSON, which makes it useful in the dashboard for onboarding a new route.

### B. Dispatch / remediation agent

This agent is defined in `dispatch.py` and is responsible for constructing the correct HTTP request, preserving auth, and retrying safely after API validation failures.

Core instruction pattern:

```text
You are an autonomous API routing agent. Formulate the exact HTTP POST request needed to create a contact/lead in the specified CRM.
Output ONLY a valid JSON object containing exactly three keys:
'url' (string), 'headers' (dictionary), and 'payload' (dictionary).
```

Additional safety constraints in the implementation include:

- never invent example.com, localhost, or placeholder domains
- prefer canonical HubSpot and Twenty endpoints
- remove rejected fields after API validation errors
- refresh doc search on retry when the endpoint is wrong
- preserve Authorization tokens across retries
- normalize payload shape for Twenty before dispatch

---

## 4. Evaluation and measured improvement

The project includes a synthetic evaluation suite in `evaluate.py` that compares a rigid baseline script to the agentic solution over 10 edge-case payloads.

### Reported comparison

| METRIC | SIMPLE BASELINE | AGENT SOLUTION | CHANGE |
| --- | --- | --- | --- |
| Success Rate | 10.0% | 100.0% | +90.0% |
| Developer time per integration | ~4 hours | ~30 seconds | -99.8% |
| Cost per evaluation | $0.00 | $0.00 | No cost increase |

The test suite includes corrupted keys, missing values, null emails, slang aliases, nested metadata, and contradictory notes.

---

## 5. Improvement changelog

This changelog captures the real story of the product’s evolution and the evidence behind each decision.

| STAGE | WHAT YOU TRIED AND WHY | EVIDENCE | DECISION / LEARNING |
| --- | --- | --- | --- |
| Baseline | Hardcoded Python mapping based on exact key matches. | Failed immediately with typo-heavy payloads such as `sellr_phne`. | Highlighted the need for semantic field mapping. |
| Iteration 1 | Switched to local LLM-based mapping. | The model mapped misspelled fields but APIs still rejected invalid values such as `"email": "None"`. | The system required a validation and retry layer. |
| Iteration 2 | Added autonomous self-correction. | A HubSpot `400 Bad Request` was read, the invalid email was removed, and the request succeeded. | Kept. Validation feedback was essential for robust dispatch. |
| Iteration 3 (Removed) | Let the agent guess the CRM URL from memory alone. | The model hallucinated a fake endpoint like `api.example.com`, causing a DNS failure. | Removed. Small local models cannot reliably memorize canonical URLs. |
| Iteration 4 | Added a fixed API endpoint directory in the prompt. | The agent successfully routed to the correct canonical URL. | Kept. Grounding the model with trusted route boundaries reduced fatal mistakes. |
| Iteration 5 | Added DuckDuckGo web search support. | Unknown CRMs were able to discover onboarding docs and endpoints dynamically. | Kept. This expanded flexibility without hardcoding every CRM. |
| Iteration 6 | Added canonical CRM enforcement and placeholder URL rejection. | Example/localhost/test domains were blocked before any HTTP request was made. | Kept. This eliminated the riskiest hallucination path. |
| Iteration 7 | Added environment-driven Airtable field mapping. | Airtable field names were aligned from `AIRTABLE_FIELD_NAMES` instead of a fragile fixed field order. | Kept. This made the system portable across schemas. |
| Iteration 8 | Added Twenty payload normalization and auth retention across retries. | Twenty rejected nested objects until the payload was rebuilt correctly; bearer tokens stayed attached on retry. | Kept. Correct payload shape and auth continuity are critical. |
| Iteration 9 | Added doc refresh and retry loop for unknown CRMs with a 5-attempt cap. | The agent re-read docs between attempts and changed route candidates rather than repeating stale guesses. | Kept. This was the safest strategy for unfamiliar CRM integrations. |
| Final | Added SQLite memory, quarantine review, and strict validation of both endpoint and payload contracts. | Repeat payloads processed faster, real data survived retries, and unrecoverable failures were safely parked for review. | Final architecture balances autonomy with safe execution boundaries. |

---

## 6. Main failure mode and hot take

### Failure mode

The original system had two major failure patterns:

1. Endpoint hallucination
   - the model invented placeholder domains like `https://api.example.com/...`
   - this caused network resolution failures and invalid dispatch attempts

2. Payload schema mismatch
   - the model produced values with wrong shape or dropped real data on retry
   - for systems like Twenty, an HTTP call could succeed while creating an empty or incomplete lead

### Hot take

Local models are great at semantic mapping and recovery, but they are not reliable enough to memorize exact canonical CRM URLs or generate precise nested schemas in the same way that a strongly typed CRM API expects.

The safest design is to let the model reason about mapping while keeping the final network path and payload contract grounded in evidence:

- valid endpoint enforcement
- web-search fallback for unknown CRMs
- protected auth retention across retries
- schema-aware payload reconstruction
- human review when retries are exhausted

---

## 7. Reproduction guide (clean environment)

This guide is written for someone starting from scratch.

### System requirements

- OS: Windows 10/11, macOS, or Ubuntu 22.04+
- Python: 3.10 or newer
- Local model engine: Ollama
- Optional hardware: NVIDIA GPU with 8GB+ VRAM or Apple Silicon
- Approximate runtime: 8-15 seconds per lead evaluation, depending on hardware
- Cost: $0.00 local inference if running entirely on Ollama

### Step 1: install Ollama and pull the model

```bash
ollama pull qwen2.5:14b
```

### Step 2: clone the repository and create a virtual environment

```bash
git clone https://github.com/YOUR_USERNAME/agentic-lead-gateway.git
cd agentic-lead-gateway
python -m venv venv
```

Windows PowerShell:

```powershell
venv\Scripts\Activate.ps1
```

Linux/macOS:

```bash
source venv/bin/activate
```

### Step 3: install dependencies

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Step 4: configure environment variables

Copy the example environment file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Check that `.env` contains the correct settings, especially:

```env
OLLAMA_BASE_URL=http://localhost:11434/v1
OLLAMA_API_KEY=ollama
OLLAMA_MODEL=qwen2.5:7b
```

### Step 5: start the application

```bash
python main.py
```

The app will initialize SQLite and serve the dashboard at:

```text
http://localhost:8000/
```

### Step 6: run the evaluation suite

```bash
python evaluate.py
```

This script compares the baseline mapper and the agentic mapper across the 10 synthetic cases. The output is a CLI summary showing which cases passed and the average success rate.

### Expected output

You should see a result like:

- baseline success rate close to 10%
- agent success rate close to 100%
- summary of each test case and pass/fail status

### Baseline and test data required

The project is designed to run with synthetic payload data that mirrors messy lead data from real-world inbound lead sources. Example fields include:

- `first_name`, `last_name`
- `phone`, `cell`, `mobile`
- `email`, `mail_addr`
- `address`, `property_location`
- `notes`, `call_summary`, `raw_agent_memo`

This is enough to evaluate semantic mapping quality and dispatch safety without needing a live CRM account.

---

## 8. Solution video outline (up to 5 minutes)

The project includes a brief video script in the root folder, but the project documentation here captures the structure and story for that presentation.

### Video flow

1. Problem introduction
   - inbound leads arrive in messy formats
   - developers manually map keys to CRM schema
   - integrating a new CRM is expensive and fragile

2. Baseline demonstration
   - a simple rigid mapping script fails on typos and malformed values
   - show the low success rate versus the intended CRM target

3. Agentic lead ingestion walkthrough
   - create a route in the UI
   - provide a raw payload with non-standard keys
   - show the agent reading the data and mapping semantically

4. Validation and retry logic
   - show a 400 error from the API
   - demonstrate the self-correction loop removing invalid values and retrying

5. Final comparison and changelog
   - present the success improvement table
   - explain the biggest contributing change: endpoint + payload validation with retry loop
   - mention one experiment that was removed: free-form URL hallucination from memory

---

## 9. Representative agent trajectories

Representative trajectories are stored in `trajectories/agent_trajectories.md` and saved JSON run logs in the `trajectories/` folder.

### Example trajectory themes

#### Onboarding agent

- Searches the web for CRM onboarding and field docs
- Reads live snippets from DuckDuckGo
- Converts that into onboarding instructions and standard field names
- Returns a structured JSON response for the dashboard UI

#### Dispatch agent

- Receives a messy payload and target CRM
- Uses memory or LLM semantic inference to build the first request
- Checks for endpoint validity and canonical route enforcement
- Dispatches the request
- Reads the response, removes invalid fields, and retries
- Escalates to quarantine if the recovery threshold is exhausted

This gives the system both explainability and operational control.

---

## 10. Files included for running the project

The project is designed to run from the root of the `lead-gateway` folder with the following files:

- `main.py`
- `agent.py`
- `agent_helper.py`
- `dispatch.py`
- `db.py`
- `evaluate.py`
- `requirements.txt`
- `.env.example`
- `static/index.html`
- `trajectories/agent_trajectories.md`
- `test_dispatch.py`

These are the project artifacts needed to reproduce the behavior, inspect the trajectories, and validate the system.

---

## 11. Final summary

This project demonstrates a practical pattern for agentic workflows in a real business context:

- local-first AI reasoning
- grounded API routing
- live documentation fallback for unknown systems
- human checkpointing for safety
- observability and memory for operational resilience

The result is a lead dispatch platform that is safer and faster than rigid integration scripts, while still remaining transparent enough to debug and audit.
