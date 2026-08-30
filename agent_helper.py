# agent_helper.py
import os
import json
from openai import OpenAI
from dotenv import load_dotenv
from duckduckgo_search import DDGS

load_dotenv()

client = OpenAI(
    base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    api_key=os.getenv("OLLAMA_API_KEY", "ollama")
)
# Ensure we are using the upgraded Qwen 2.5 model
MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")

PRESET_CRMS = {
    "hubspot": {
        "instructions": (
            "1. Click the Settings gear icon in the top navigation bar.\n"
            "2. In the left sidebar, navigate to 'Integrations' > 'Private Apps'.\n"
            "3. Click the orange 'Create a private app' button.\n"
            "4. Name it 'LeadGateway' and navigate to the 'Scopes' tab.\n"
            "5. Check 'crm.objects.contacts.write' and 'crm.objects.contacts.read'.\n"
            "6. Click 'Create app' > 'Continue creating', then copy the token starting with 'pat-...'"
        ),
        "expected_fields": ["firstname", "lastname", "phone", "email", "address", "notes"]
    },
    "resimpli": {
        "instructions": (
            "1. Log in to your REsimpli account.\n"
            "2. Click your Profile picture / Company Settings on the bottom sidebar.\n"
            "3. Navigate to 'Integrations & API'.\n"
            "4. Generate a new API Access Token or Webhook URL and copy it below."
        ),
        "expected_fields": ["First Name", "Last Name", "Phone", "Email", "Property Address", "Asking Price", "Notes"]
    },
    "gohighlevel": {
        "instructions": (
            "1. Log in to your GoHighLevel sub-account.\n"
            "2. Navigate to 'Settings' > 'Business Profile' to copy your Location API Key (v2 Token).\n"
            "3. (Alternative) Under Automations > Workflows, create an Inbound Webhook trigger and paste the Webhook URL below."
        ),
        "expected_fields": ["first_name", "last_name", "phone", "email", "address1", "notes"]
    }
}

def search_crm_onboarding_docs(crm_name: str) -> str:
    """Searches the web for instructions and field mappings for unknown CRMs."""
    print(f"\n[AGENT TOOL]: Searching web for '{crm_name}' onboarding documentation...")
    try:
        snippets = []
        with DDGS() as ddgs:
            # Query 1: How to authenticate / get tokens
            auth_results = ddgs.text(f"{crm_name} CRM how to get API key access token webhook documentation", max_results=2)
            for idx, res in enumerate(auth_results):
                snippets.append(f"Auth Source {idx + 1}: {res['title']}\nDetails: {res['body']}")
                
            # Query 2: Standard fields / schema properties
            schema_results = ddgs.text(f"{crm_name} CRM API contact lead standard properties fields", max_results=2)
            for idx, res in enumerate(schema_results):
                snippets.append(f"Schema Source {idx + 1}: {res['title']}\nDetails: {res['body']}")
                
        return "\n\n".join(snippets)
    except Exception as e:
        return f"Web search failed: {str(e)}"

def get_crm_integration_details(crm_name: str) -> dict:
    normalized = crm_name.strip().lower()

    # 1. Exact preset match for the most common platforms to guarantee accuracy and speed
    for key, data in PRESET_CRMS.items():
        if key in normalized:
            return data

    # 2. Dynamic Tool Usage: Trigger live web search for unknown CRMs
    live_search_results = search_crm_onboarding_docs(crm_name)

    prompt = f"""
You are an expert CRM Integration Engineer. The user wants to integrate '{crm_name}'.

LIVE WEB SEARCH RESULTS FOR '{crm_name}':
{live_search_results}

Using the live search results above (if helpful) or your internal knowledge, provide concise, numbered, UI-specific steps on how to find the API Access Token, Private App Token, or Inbound Webhook URL in {crm_name}.
Also, carefully review the search results to infer a list of standard string field names this CRM uses for a real estate lead or contact (e.g., ["first_name", "last_name", "phone", "email"]).

Return ONLY valid JSON matching this exact schema:
{{
  "instructions": "1. Step one...\\n2. Step two...\\n3. Step three...",
  "expected_fields": ["first_name", "last_name", "phone", "email", "address", "notes"]
}}
"""
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,  # Strict adherence to instructions
            response_format={"type": "json_object"}
        )
        return json.loads(response.choices[0].message.content.strip())
    except Exception:
        # Fallback in case of severe LLM hallucination or crash
        return {
            "instructions": f"1. Log in to your {crm_name} account.\n2. Navigate to Settings > Integrations / API.\n3. Generate a Personal Access Token with read/write contacts permissions and paste it below.",
            "expected_fields": ["first_name", "last_name", "phone", "email", "address", "notes"]
        }