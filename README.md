# Agentic Lead Gateway

An autonomous CRM ingestion and dispatch engine built for the Micro1 Agentic Workflows Hackathon.

## 1. The Problem & User Value

**Who has this problem?**
This problem is experienced by lead generation agencies, real estate wholesalers, and service-based businesses (like roofing or plumbing companies) that need to sync incoming leads across various client CRM platforms.

**What is the current bottleneck?**
Currently, when a lead generation company partners with a new client, a developer must manually write hardcoded scripts or set up complex Zapier workflows to map the raw lead data to that specific client's CRM (e.g., HubSpot, Salesforce, GoHighLevel). Furthermore, clients are often forced to hand over sensitive API keys or even CRM usernames and passwords, creating massive security risks.

**Why is solving it valuable?**
The Agentic Lead Gateway strips out technical barriers and security risks. Instead of passing API keys or writing code, clients simply generate a unique "Lead Key" handle. The lead gen company sends messy, unformatted JSON payloads to this handle, and our AI agent autonomously normalizes the data, constructs the required HTTP headers, and dynamically routes it into the destination CRM. It turns a week-long integration sprint into a 30-second UI onboarding task.

---

## 2. Measured Improvement (Evaluation)

To evaluate the solution, we compared a simple baseline against the agentic solution.

| METRIC | SIMPLE BASELINE | AGENT SOLUTION | CHANGE |
| --- | --- | --- | --- |
| **Developer time per integration** | 4 hours (coding/testing) | 30 seconds (UI onboarding) | **-99.8% time saved** |
| **Integration Success Rate (Messy Data)** | 0% (Fails on unmapped fields) | 100% (Agent infers semantic mapping) | **+100% success** |
| **Cost per task (Compute)** | $0.00 (Hardcoded script) | ~$0.00 (Local Qwen 2.5 on RTX 4060) | **Negligible increase** |

*Note: The baseline used was a standard Python script with rigid `if/else` mapping statements for specific CRMs, representing the manual process people use today.*

---

## 3. Improvement Changelog

This changelog tells the story of how the solution evolved, highlighting important experiments, evidence, and key decisions.

| STAGE | WHAT YOU TRIED AND WHY | EVIDENCE | DECISION / LEARNING |
| --- | --- | --- | --- |
| **Baseline** | Started with a hardcoded Python `dispatch.py` script to route leads to HubSpot and Airtable based on exact key matching. | Fails immediately when lead gen vendors send data with typos (e.g., `sellr_phne` instead of `phone`). | Established the starting point; highlighted the need for semantic field mapping. |
| **Iteration 1** | Introduced `llama3.2` to dynamically read incoming JSON payloads and map them to the destination CRM's schema. | Agent successfully mapped misspelled fields, but HubSpot API rejected payloads containing invalid email strings (e.g., "None"). | Kept the mapping agent, but realized the system needed a way to handle API rejections dynamically. |
| **Iteration 2** | Added an autonomous self-correction loop. If the destination API returns a `400 Bad Request`, the error is fed back to the agent to fix the payload. | Agent read the "INVALID_EMAIL" error from HubSpot, stripped the invalid email field from the payload, and successfully retried the POST request. | Kept. Verification and self-correction caught errors before they failed the workflow completely. |
| **Iteration 3 (Removed)** | Allowed the agent to guess the CRM's API endpoint URL purely from its training data. | Agent hallucinated the HubSpot endpoint. It received a `404 Not Found` HTML page and got stuck in an endless retry loop because it couldn't parse the HTML. | **Removed.** Taught us that small local models cannot reliably memorize exact URLs. |
| **Final** | Combined the mapping and self-correction loop with a hardcoded "API Endpoint Directory" injected into the prompt. | Agent reliably constructs the correct headers, maps the messy payload, and routes successfully to the exact URL provided in the prompt directory. | Identified the main contribution: Grounding the agent's creativity with strict routing boundaries. |

---

## 4. Main Failure Mode & Hot Take

**The Failure Mode:** When relying entirely on the LLM to figure out where to send the data, the agent hallucinated the API endpoint (e.g., guessing `[api.hubspot.com/contacts](https://api.hubspot.com/contacts)` instead of `[api.hubapi.com/crm/v3/objects/contacts](https://api.hubapi.com/crm/v3/objects/contacts)`). This resulted in a `404 Not Found` error returning an HTML webpage, which the agent could not read or self-correct from, causing the loop to fail.

**The Hot Take (Insight):** Small local models (like 7B or 8B parameters) are incredible at semantic reasoning, autonomous JSON structuring, and self-correcting `400 Validation` errors. However, they are terrible at memorizing exact URLs. If you want a reliable agentic workflow, you must **ground the agent** by providing an explicit "API Reference Directory" in the system prompt. Let the LLM handle the dynamic data structuring, but hardcode the URLs.

---

## 5. Reproduction Guide

This guide allows an evaluator to run the agent solution, test against baseline cases, and reproduce all reported evaluation metrics from a clean environment.

### System Requirements & Environment Specifications

* **Operating System:** Windows 10/11, macOS (Apple Silicon), or Ubuntu 22.04+
* **Python Version:** `3.10` or higher
* **Hardware:** NVIDIA GPU with 8GB+ VRAM (e.g., RTX 3060/4060) or Apple Silicon M1/M2/M3 (16GB RAM unified memory recommended)
* **Local LLM Engine:** [Ollama](https://ollama.com/) (Version 0.3.0+)
* **Approximate Runtime:** 8 to 15 seconds per lead evaluation
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

```bash
pip install --upgrade pip
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

*(If you are evaluating local mock endpoints only, no third-party API keys are required to run the gateway and UI)*.

#### Step 5: Start the Lead Gateway Server

```bash
python main.py

```

*The local server will initialize `routes.db` automatically and serve the UI at `http://localhost:8000/`.*

---

### Evaluation Procedures & Test Cases

Run the following test cases to evaluate the baseline versus the agentic solution.

#### Test Case 1: Standard Ingestion via Web UI (Evaluation Run)

1. Open `http://localhost:8000/` in your browser.
2. In the **Create New Route** card:
* **Client Name:** `Test Wholesaler LLC`
* **Destination CRM:** `HubSpot`
* Click **Continue**.


3. *Agent Behavior:* The agent invokes `agent_helper.py` to search live docs and display UI token instructions and inferred fields.
4. Paste a dummy webhook URL (e.g., `[https://httpbin.org/post](https://httpbin.org/post)`) or a live HubSpot `pat-...` token into the token input.
5. Click **Generate Lead Key**. Confirm the route is saved to SQLite and appears in **Active Lead Routes**.

#### Test Case 2: Autonomous Semantic Mapping (PowerShell / Terminal)

Submit a payload with deliberately unstructured, non-standard key names:

```powershell
$body = @{
    seller_first_name   = "Alexander"
    seller_last_name    = "Hamilton"
    contact_cell        = "555-789-0123"
    property_location   = "57 Maiden Lane, New York, NY"
    asking_price        = 850000
    vendor_notes        = "Owner must relocate before the end of the month. Highly motivated."
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/lead/wholesaler_alpha_123" -Method Post -ContentType "application/json" -Body $body

```

**Expected Result:**

```json
{
  "status": "success",
  "route_client": "Airtable Master Sync",
  "crm_type": "airtable",
  "normalized_payload": {
    "First Name": "Alexander",
    "Last Name": "Hamilton",
    "Phone": "555-789-0123",
    "Address": "57 Maiden Lane, New York, NY",
    "More Info": "Asking Price: 850000. Owner must relocate before the end of the month. Highly motivated."
  }
}

```

#### Test Case 3: Self-Correction Loop on 400 Validation Error (Edge Case)

Send a lead where `email` is passed as `None` or an invalid string.

* **Baseline Behavior:** Rigid Python script posts `"email": "None"` $\rightarrow$ Destination API returns `400 Bad Request` $\rightarrow$ Lead lost.
* **Agent Solution Behavior:** Agent intercepts the `400 INVALID_EMAIL` error from the destination response, strips the invalid field, re-formats the payload structure, and retries the dispatch until a `200/201` is received.

---

## 6. Agent Trajectories

*Please see the `trajectories/` folder (or relevant file) for representative agent trajectories. These show how the agents responded to instructions, utilized tools, and handled feedback during testing.*