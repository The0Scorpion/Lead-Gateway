# dispatch.py
import os
import json
import re
import requests
from datetime import datetime, timezone
from openai import OpenAI
from dotenv import load_dotenv
from duckduckgo_search import DDGS
from db import get_memory, save_memory

load_dotenv()

client = OpenAI(
    base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    api_key=os.getenv("OLLAMA_API_KEY", "ollama")
)
MODEL_NAME = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
HUBSPOT_CONTACTS_URL = "https://api.hubapi.com/crm/v3/objects/contacts"


def _save_trajectory(trajectory: list, trajectory_id: str | None):
    if not trajectory_id:
        return
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", trajectory_id)
    trajectory_dir = os.path.join(os.path.dirname(__file__), "trajectories")
    os.makedirs(trajectory_dir, exist_ok=True)
    file_path = os.path.join(
        trajectory_dir,
        f"{safe_id}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json",
    )
    with open(file_path, "w", encoding="utf-8") as trajectory_file:
        json.dump(trajectory, trajectory_file, indent=2)


def _format_destination_error(crm_name: str, status_code: int, response_text: str) -> str:
    display_name = "HubSpot" if crm_name.lower() == "hubspot" else crm_name.title()
    try:
        error = json.loads(response_text)
    except (TypeError, json.JSONDecodeError):
        return f"{display_name} returned HTTP {status_code}: {response_text}"

    details = []
    for item in error.get("errors", []):
        message = item.get("message") or item.get("localizedErrorMessage")
        if message:
            details.append(message)
    message = error.get("message")
    if message and not details:
        details.append(message)
    readable = "; ".join(dict.fromkeys(details)) or "The destination rejected the lead."
    return f"{display_name} rejected the lead (HTTP {status_code}): {readable} Technical details: {response_text}"


def format_stored_error(crm_name: str, error_text: str) -> str:
    match = re.search(r"Last error \((\d+)\): (.*)$", error_text, re.DOTALL)
    if match:
        return _format_destination_error(crm_name, int(match.group(1)), match.group(2))
    return error_text


def _hubspot_payload(mapped_data: dict) -> dict:
    missing_values = {"", "none", "null", "n/a", "na"}
    unsupported_contact_properties = {"notes"}
    cleaned = {
        key: value
        for key, value in (mapped_data or {}).items()
        if value is not None
        and not (isinstance(value, str) and value.strip().lower() in missing_values)
        and key.lower() not in unsupported_contact_properties
    }
    return {"properties": cleaned}


def _remove_rejected_hubspot_properties(payload: dict, error_text: str) -> tuple:
    rejected = set(re.findall(r'Property ["\\\']([^"\\\']+)["\\\'] does not exist', error_text, re.IGNORECASE))
    rejected.update(re.findall(r'"propertyName"\s*:\s*\[\s*["\\\']([^"\\\']+)', error_text, re.IGNORECASE))
    if "INVALID_EMAIL" in error_text.upper():
        rejected.add("email")

    properties = payload.get("properties", {}) if isinstance(payload, dict) else {}
    removed = {key for key in properties if key.lower() in {name.lower() for name in rejected}}
    return {"properties": {key: value for key, value in properties.items() if key not in removed}}, removed


def _resolve_known_crm_request(crm_name: str, user_provided_target: str, mapped_data: dict) -> tuple:
    normalized = crm_name.lower().strip()
    if normalized == "hubspot":
        token = user_provided_target.strip()
        if token.startswith("http://") or token.startswith("https://"):
            if token.startswith(HUBSPOT_CONTACTS_URL):
                url = token
            else:
                raise ValueError("HubSpot requires a private app PAT token, not a webhook URL or arbitrary API host.")
        else:
            url = HUBSPOT_CONTACTS_URL
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        payload = _hubspot_payload(mapped_data)
        return url, headers, payload

    if user_provided_target.startswith("http://") or user_provided_target.startswith("https://"):
        return user_provided_target, {"Content-Type": "application/json"}, mapped_data

    return user_provided_target, {"Content-Type": "application/json"}, mapped_data


def push_to_airtable(mapped_data: dict) -> dict:
    api_key = os.getenv("AIRTABLE_API_KEY")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")
    url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json={"fields": mapped_data}, timeout=10)
    if response.status_code not in [200, 201]:
        raise Exception(f"Airtable API error ({response.status_code}): {response.text}")
    return response.json()

def dispatch_agentic(crm_name: str, user_provided_target: str, mapped_data: dict, trajectory_id: str | None = None) -> tuple:
    """
    Autonomous dispatcher with Agent Memory, Tool Search, and Trajectory Recording.
    Returns: (response_json, trajectory_log_array)
    """
    trajectory = []
    normalized_crm = crm_name.lower().strip()
    cached_mapping = None

    if normalized_crm == "hubspot":
        url, headers, payload = _resolve_known_crm_request(normalized_crm, user_provided_target, mapped_data)
        trajectory.append({"step": "HubSpot Route Enforcement", "url": url, "detail": "Using the official HubSpot contacts endpoint and bearer token auth."})
    else:
        # 1. Check Agent Memory
        input_keys_signature = "-".join(sorted(mapped_data.keys()))
        cached_mapping = get_memory(crm_name, input_keys_signature)

        if cached_mapping:
            trajectory.append({"step": "Memory Lookup", "status": "HIT", "detail": f"Retrieved cached schema mapping for signature: {input_keys_signature}"})
            payload = json.loads(cached_mapping)
        else:
            trajectory.append({"step": "Memory Lookup", "status": "MISS", "detail": "Executing semantic mapping via LLM..."})

            system_prompt = (
                "You are an autonomous API routing agent. Formulate the exact HTTP POST request "
                "needed to create a contact/lead in the specified CRM. "
                "Output ONLY a valid JSON object containing exactly three keys: "
                "'url' (string), 'headers' (dictionary), and 'payload' (dictionary)."
            )

            known_endpoints = {
                "hubspot": "URL: https://api.hubapi.com/crm/v3/objects/contacts (Requires payload wrapped in {\"properties\": {...}})",
                "twenty": "URL: https://api.twenty.com/rest/people",
                "freshsales": "URL: https://domain.myfreshworks.com/crm/sales/api/contacts"
            }

            if normalized_crm in known_endpoints:
                api_directory = f"KNOWN CRM ENDPOINT:\n- {crm_name}: {known_endpoints[normalized_crm]}"
            else:
                trajectory.append({"step": "Tool Execution", "tool": "DuckDuckGoSearch", "detail": f"Searching web for {crm_name} endpoint docs"})
                try:
                    with DDGS() as ddgs:
                        results = ddgs.text(f"{crm_name} CRM API create contact endpoint documentation", max_results=2)
                        snippets = [f"{r['title']}: {r['body']}" for r in results]
                        api_directory = f"LIVE WEB SEARCH FOR '{crm_name}':\n" + "\n".join(snippets)
                except Exception:
                    api_directory = "Use generic endpoint structure."

            initial_prompt = f"""
            CRM/Platform: {crm_name}
            Target / Token: {user_provided_target}
            Data to Send:
            {json.dumps(mapped_data, indent=2)}
            {api_directory}
            Rules:
            1. If 'Target' is a URL (starts with http), use it as 'url'.
            2. If 'Target' is a token, look at the directory to find the API 'url'.
            3. Formulate correct 'headers' (e.g., Authorization: Bearer <token>).
            4. Structure 'payload' properly, omitting null values.
            5. For HubSpot, the only valid URL is https://api.hubapi.com/crm/v3/objects/contacts.
            """

            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": initial_prompt}],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            request_config = json.loads(response.choices[0].message.content.strip())
            url = request_config.get("url")
            headers = request_config.get("headers", {})
            payload = request_config.get("payload", {})

            trajectory.append({"step": "LLM Initial Request Formulation", "url": url, "payload": payload})
            save_memory(crm_name, input_keys_signature, json.dumps(payload))

            if normalized_crm == "hubspot":
                url, headers, payload = _resolve_known_crm_request(normalized_crm, user_provided_target, mapped_data)
                trajectory.append({"step": "HubSpot Route Override", "url": url, "detail": "Overrode model-generated endpoint to the official HubSpot API."})

        if normalized_crm == "hubspot" and cached_mapping:
            url, headers, payload = _resolve_known_crm_request(normalized_crm, user_provided_target, mapped_data)
            trajectory.append({"step": "HubSpot Memory Rebuild", "url": url, "detail": "Rebuilt the HubSpot request using the canonical endpoint and PAT auth."})

    # Execution & Self-Correction Loop
    max_retries = 2
    for attempt in range(max_retries + 1):
        # Construct full config if loaded from memory
        if cached_ng := locals().get('cached_mapping'):
            # Rebuild url and headers from target
            url = "https://api.hubapi.com/crm/v3/objects/contacts" if "hubspot" in crm_name.lower() else user_provided_target
            headers = {"Authorization": f"Bearer {user_provided_target.strip()}", "Content-Type": "application/json"}
            payload = json.loads(cached_ng)
            
        headers["Content-Type"] = "application/json"
        
        trajectory.append({"step": f"HTTP Dispatch Attempt {attempt + 1}", "url": url})
        try:
            api_response = requests.post(url, headers=headers, json=payload, timeout=15)
        except requests.RequestException as error:
            readable_error = f"{normalized_crm.title()} could not be reached: {error}"
            trajectory.append({"step": "Network Failure", "error": readable_error})
            _save_trajectory(trajectory, trajectory_id)
            raise Exception(readable_error) from error
        
        if api_response.status_code in [200, 201, 202, 204]:
            trajectory.append({"step": "Dispatch Success", "status_code": api_response.status_code})
            _save_trajectory(trajectory, trajectory_id)
            try:
                return api_response.json(), trajectory
            except Exception:
                return {"status": "success", "status_code": api_response.status_code}, trajectory
        
        if attempt < max_retries:
            readable_error = _format_destination_error(normalized_crm, api_response.status_code, api_response.text)
            trajectory.append({"step": "Self-Correction Triggered", "error_code": api_response.status_code, "error": readable_error, "response": api_response.text})
            if normalized_crm == "hubspot":
                payload, removed_properties = _remove_rejected_hubspot_properties(payload, api_response.text)
                if removed_properties:
                    trajectory.append({
                        "step": "Deterministic HubSpot Field Removal",
                        "removed_properties": sorted(removed_properties),
                    })
            correction_prompt = f"""
            API request failed with status {api_response.status_code}.
            Human-readable error: {readable_error}
            Technical API response: {api_response.text}
            Current payload:
            {json.dumps(payload)}
            Fix only the payload fields identified by the API error. Do not re-add rejected fields.
            Return ONLY JSON with 'url', 'headers', 'payload'.
            """
            corr_resp = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "system", "content": "Return ONLY JSON."}, {"role": "user", "content": correction_prompt}],
                temperature=0.0,
                response_format={"type": "json_object"}
            )
            corrected = json.loads(corr_resp.choices[0].message.content.strip())
            if normalized_crm == "hubspot":
                # The model may repair fields, but it must never choose HubSpot's endpoint.
                _, headers, corrected_payload = _resolve_known_crm_request(
                    normalized_crm, user_provided_target, mapped_data
                )
                url = HUBSPOT_CONTACTS_URL
                payload = {"properties": corrected_payload["properties"]}
                if isinstance(corrected.get("payload"), dict):
                    candidate_payload = corrected["payload"]
                    if isinstance(candidate_payload.get("properties"), dict):
                        payload = _hubspot_payload(candidate_payload["properties"])
                payload, _ = _remove_rejected_hubspot_properties(payload, api_response.text)
            else:
                url = corrected.get("url", url)
                headers = corrected.get("headers", headers)
                payload = corrected.get("payload", payload)
        else:
            readable_error = _format_destination_error(normalized_crm, api_response.status_code, api_response.text)
            trajectory.append({"step": "Execution Failed", "error": readable_error, "response": api_response.text})
            _save_trajectory(trajectory, trajectory_id)
            raise Exception(f"Agent exhausted retries. {readable_error}")
            
    return {}, trajectory