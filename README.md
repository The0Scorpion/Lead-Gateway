# Agentic Lead Gateway

An autonomous CRM ingestion and dispatch engine built for the Micro1 Agentic Workflows Hackathon.

## Core Features

To ensure enterprise-grade reliability and comply with safe Al deployment guidelines, this gateway incorporates:

* **Semantic Inference Mapping:** Uses `qwen2.5:14b` to dynamically map messy, non-standard lead data to strict CRM schemas.
* **Autonomous Web Search:** Uses `duckduckgo-search` to actively hunt for API documentation and UI instructions for unknown CRMs.
* **Agent Memory:** Caches successful schema transformations in SQLite to bypass LLM latency on repeat payload structures.
* **Human-in-the-Loop (Quarantine):** Intercepts unrecoverable API errors (e.g., exhausted retries) and routes them to a manual review queue to ensure consequential actions are controlled.


* **Live Trajectory UI:** Exposes the agent's step-by-step reasoning, memory checks, and tool calls directly in the dashboard.

---

## 1. The Problem & User Value

**Who has this problem?**
This problem is experienced by lead generation agencies, real estate wholesalers, and service-based businesses (like roofing or plumbing companies) that need to sync incoming leads across various client CRM platforms.

**What is the current bottleneck?**
Currently, when a lead generation company partners with a new client, a developer must manually write hardcoded scripts or set up complex Zapier workflows to map the raw lead data to that specific client's CRM (e.g., HubSpot, Salesforce, GoHighLevel). Furthermore, clients are often forced to hand over sensitive API keys or even CRM usernames and passwords, creating massive security risks.

**Why is solving it valuable?**
The Agentic Lead Gateway strips out technical barriers and security risks. Instead of passing API keys or writing code, clients simply generate a unique "Lead Key" handle. The lead gen company sends messy, unformatted JSON payloads to this handle, and our AI agent autonomously normalizes the data, constructs the required HTTP headers, and dynamically routes it into the destination CRM. It turns a week-long integration sprint into a 30-second UI onboarding task.

---

## 2. Measured Improvement (Evaluation)

To evaluate the solution, we compared a rigid baseline script against our agentic solution across **10 synthetic test cases**, including highly corrupted edge cases (e.g., numerical index keys, null emails, slang abbreviations).

| METRIC | SIMPLE BASELINE | AGENT SOLUTION | CHANGE |
| --- | --- | --- | --- |
| **Primary Outcome (Success Rate)** | 10.0% (Failed on unmapped keys) | 100.0% (Inferred semantic meaning) | **+90.0% success** |
| **Developer time per integration** | ~4 hours (coding/testing) | ~30 seconds (UI onboarding) | **-99.8% time saved** |
| **Cost Per Evaluation** | $0.00 (Hardcoded script) | $0.00 (Local Qwen 2.5 14B) | **No cost increase** |

*Note: The baseline used was a standard Python script with rigid `if/else` mapping statements for specific CRMs, representing the manual process people use today.*

---

## 3. Improvement Changelog

This changelog tells the story of how the solution evolved, highlighting important experiments, evidence, and key decisions.

| STAGE | WHAT YOU TRIED AND WHY | EVIDENCE | DECISION / LEARNING |
| --- | --- | --- | --- |
| **Baseline** | Hardcoded Python `dispatch.py` script based on exact key matching. | Failed immediately when vendors sent data with typos (e.g., `sellr_phne`). | Highlighted the need for semantic field mapping. |
| **Iteration 1** | Local LLM for dynamic JSON mapping. | Mapped misspelled fields successfully, but APIs rejected invalid strings (e.g., `"email": "None"`). | Realized the system needed a way to handle API rejections dynamically. |
| **Iteration 2** | Added autonomous self-correction loop. | Agent read a `400 Bad Request` error from HubSpot, stripped the invalid email field, and retried successfully. | Kept. Verification and self-correction caught errors before they failed the workflow completely. |
| **Iteration 3 (Removed)** | Allowed the agent to guess the CRM's API endpoint URL purely from training data. | Agent hallucinated the endpoint as `api.example.com`, causing a fatal `NameResolutionError` / DNS failure. | **Removed.** Small models cannot reliably memorize exact URLs. |
| **Iteration 4** | Hardcoded an "API Endpoint Directory" injected into the prompt. | Agent reliably mapped the messy payload and routed successfully to the exact canonical URL provided. | Grounding the agent's creativity with strict routing boundaries prevents fatal HTTP resolution errors. |
| **Iteration 5** | Integrated DuckDuckGo web search tool. | For unknown CRMs, the agent successfully scraped live docs to find onboarding steps and endpoints. | Kept. Greatly expanded the system's flexibility without hardcoding every CRM in existence. |
| **Iteration 6** | Added canonical CRM enforcement and placeholder URL rejection. | Requests to `example.com`, `localhost`, and similar fake domains were blocked before any network call. | Kept. This eliminates the most dangerous form of model hallucination. |
| **Iteration 7** | Added env-driven Airtable schema mapping. | Airtable columns now map from `AIRTABLE_FIELD_NAMES` instead of a rigid hardcoded field order. | Kept. This makes the system portable across different Airtable base schemas. |
| **Iteration 8** | Added CRM-aware payload normalization for Twenty and auth retention across retries. | Twenty rejected nested `name`/`emails`/`phones` objects until the payload was reconstructed correctly; bearer tokens were preserved during retry. | Kept. Correct payload shape and auth continuity are critical for successful lead creation. |
| **Iteration 9** | Added doc-driven retries for unknown CRMs with a 5-attempt cap and live doc refresh. | The agent re-read documentation between attempts and tried new endpoints instead of repeating stale guesses. | Kept. This is the safest path for supporting unfamiliar CRM integrations. |
| **Final** | Added SQLite Agent Memory, quarantine handling, and strict validation for both endpoint and payload contracts. | Repeat payloads processed faster, real values survived retries, and unrecoverable errors were safely parked for manual review. | Final architecture combines autonomous reasoning with safe, controlled execution boundaries. |

---

## 4. Main Failure Mode & Hot Take

**The Failure Mode:** The original system could fail in two different ways: endpoint hallucination and payload-schema mismatch. The most obvious issue was the model inventing placeholder URLs such as `https://api.example.com/...` and submitting leads to a domain that cannot resolve. We also discovered a more subtle but equally harmful issue: for Twenty, the integration could succeed on the HTTP call yet create an empty lead because the payload shape was wrong or the actual values were lost during the retry/correction loop.

**The Hot Take (Insight):** Local models are very good at semantic mapping and error recovery, but they are not reliable enough to memorize exact canonical CRM URLs or produce the precise nested object schema used by modern CRMs. The safe design is to combine: strict endpoint validation, live doc search when the CRM is unknown, schema-aware payload rebuilding, preserved bearer auth across retries, and a human review queue when recovery hits its limit. Let the model reason about mapping, but keep the network path and final payload contract grounded in real evidence.

### Latest reliability improvements

The current implementation includes several safeguards added after the original prototype:

* **Environment-aware Airtable field mapping:** `AIRTABLE_FIELD_NAMES` is read from the environment and mapped to the actual Airtable schema instead of assuming a fixed order.
* **Placeholder URL blocking:** requests to `example.com`, `localhost`, and other fake/test domains are rejected before any HTTP call is made.
* **Known CRM route enforcement:** HubSpot and Twenty are forced through their canonical routes instead of trusting free-form model output.
* **Docs-based fallback for unknown CRMs:** when a CRM is not recognized, the system searches the web, reads documentation, picks a valid endpoint candidate, and retries up to five times with refreshed docs.
* **Auth preservation on retry:** bearer tokens remain attached through the self-correction loop so retrying does not silently lose credentials.
* **Schema normalization for Twenty:** the dispatcher rebuilds `name`, `emails`, and `phones` in the exact nested structure expected by Twenty, preserving real lead data instead of replacing them with blank objects.
* **Regression coverage:** the dispatcher suite verifies Airtable env-based mapping, endpoint enforcement, auth retention, retry loop behavior, and payload-shape correctness.

---

## 5. Reproduction Guide

This guide allows an evaluator to run the agent solution, test against baseline cases, and reproduce all reported evaluation metrics from a clean environment.

### System Requirements & Environment Specifications

* **Operating System:** Windows 10/11, macOS (Apple Silicon), or Ubuntu 22.04+
* **Python Version:** `3.10` or higher
* **Hardware:** NVIDIA GPU with 8GB+ VRAM (e.g., RTX 3060/4060) + 16GB System RAM, or Apple Silicon M1/M2/M3.
* **Local LLM Engine:** [Ollama](https://ollama.com/) (Version 0.3.0+)
* **Approximate Runtime:** 8 to 15 seconds per lead evaluation (depending on CPU offloading for 14B model)
* **Cost:** **$0.00** (Local inference with zero paid API overhead)

---

### Step-by-Step Setup from a Clean Environment

#### Step 1: Pull and Run the Local Model

Ensure the Ollama daemon is running, then pull the target reasoning model:

```bash
ollama pull qwen2.5:14b

```

#### Step 2: Clone Repository & Create Virtual Environment

```bash
git clone https://github.com/YOUR_USERNAME/agentic-lead-gateway.git
cd agentic-lead-gateway

# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows (PowerShell):
venv\Scripts\Activate.ps1
# On Linux/macOS:
# source venv/bin/activate

```

#### Step 3: Install Required Dependencies

Ensure you have installed the core dependencies along with `duckduckgo-search` for the web tools:

```bash
python -m pip install --upgrade pip
pip install -r requirements.txt

```

#### Step 4: Configure Environment Variables

Copy the template file to active `.env`:

```bash
# On Windows (PowerShell):
Copy-Item .env.example .env
# On Linux/macOS:
# cp .env.example .env

```

*(Note: Verify that `OLLAMA_MODEL=qwen2.5:14b` is correctly set in your `.env` file).*

#### Step 5: Start the Lead Gateway Server

```bash
python main.py

```

*The server will self-heal the `routes.db` SQLite database automatically on startup and serve the UI at `http://localhost:8000/`.*

---

### Evaluation Procedures & Test Cases

Run the following test cases to evaluate the solution.

#### Test Case 1: Standard Ingestion via Web UI (Evaluation Run)

1. Open `http://localhost:8000/` in your browser.
2. In the **Create New Route** card:
* **Client Name:** `Test Wholesaler LLC`
* **Destination CRM:** `HubSpot`
* Click **Continue**.


3. *Agent Behavior:* The agent invokes DuckDuckGo search tools to scrape live docs and display UI token instructions and inferred schema fields.
4. Paste a dummy webhook URL (e.g., `[https://httpbin.org/post](https://httpbin.org/post)`) or a live token into the input.
5. Click **Generate Lead Key**.

#### Test Case 2: Autonomous Semantic Mapping & Trajectory Viewing

In the **Manual Lead Tester**, select your new route and submit a payload with deliberately unstructured, non-standard key names:

```json
{
    "seller_first_name": "Alexander",
    "seller_last_name": "Hamilton",
    "contact_cell": "555-789-0123",
    "property_location": "57 Maiden Lane, New York, NY",
    "asking_price": 850000,
    "vendor_notes": "Owner must relocate. Highly motivated."
}

```

**Expected Result:** The payload maps cleanly to target schema keys (e.g., `First Name`, `Phone`, `Address`). The **Agent Trajectory Log** will display directly in the UI, showing memory misses/hits and HTTP request formulation.

#### Test Case 3: The 10-Case Evaluation Suite

To reproduce the baseline vs. agent metrics reported in Section 2, run the standalone evaluation script:

```bash
python evaluate.py

```

#### Test Case 4: Human-in-the-Loop Quarantine (Edge Case)

Send a lead to an invalid destination token or a hard-down endpoint. The agent will attempt self-correction. Upon exhausting its max retries, it will safely drop the lead into the **Human-in-the-Loop Review Queue** on the dashboard for manual resolution.

---

## 6. Agent Trajectories

*Please see the `trajectories/agent_trajectories.md` file for representative agent trajectories. These document how the agents responded to instructions, utilized web search tools, and handled feedback loops during testing.*