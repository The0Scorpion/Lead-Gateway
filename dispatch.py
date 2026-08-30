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


def _remove_rejected_fields(payload: dict, error_text: str) -> tuple:
    if not isinstance(payload, dict):
        return payload, set()

    rejected = set(re.findall(r'(?:doesn\'t|does not|doesn’t)\s+have\s+any\s+["\']([^"\']+?)["\']\s+field', error_text, re.IGNORECASE))
    if not rejected:
        return payload, set()

    normalized_rejected = {name.lower() for name in rejected}
    cleaned = {
        key: value
        for key, value in payload.items()
        if key is not None and key.lower() not in normalized_rejected
    }
    return cleaned, {key for key in payload if isinstance(key, str) and key.lower() in normalized_rejected}


def _normalize_twenty_payload(mapped_data: dict) -> dict:
    if not isinstance(mapped_data, dict):
        return mapped_data

    cleaned = {}

    name_value = mapped_data.get("name")
    if isinstance(name_value, dict):
        first_name = name_value.get("firstName") or name_value.get("first_name") or name_value.get("first")
        last_name = name_value.get("lastName") or name_value.get("last_name") or name_value.get("last")
    elif isinstance(name_value, str):
        parts = [part.strip() for part in name_value.split() if part and part.strip()]
        first_name = parts[0] if parts else ""
        last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
    else:
        first_name = mapped_data.get("first_name") or mapped_data.get("firstName")
        last_name = mapped_data.get("last_name") or mapped_data.get("lastName")
    if first_name or last_name:
        cleaned["name"] = {
            "firstName": str(first_name).strip() if first_name else "",
            "lastName": str(last_name).strip() if last_name else "",
        }

    email_value = mapped_data.get("primaryEmail")
    if email_value is None:
        email_value = mapped_data.get("email") or mapped_data.get("emails")
    if isinstance(email_value, dict):
        primary = email_value.get("primaryEmail") or email_value.get("value") or email_value.get("handle") or ""
        additional = email_value.get("additionalEmails") or []
    elif isinstance(email_value, list):
        primary = str(email_value[0]).strip() if email_value else ""
        additional = [str(item).strip() for item in email_value[1:] if str(item).strip()]
    elif isinstance(email_value, str):
        primary = email_value.strip()
        additional = []
    else:
        primary = ""
        additional = []
    if primary or additional:
        cleaned["emails"] = {
            "primaryEmail": str(primary).strip(),
            "additionalEmails": [str(item).strip() for item in additional if str(item).strip()],
        }

    phone_value = mapped_data.get("primaryPhoneNumber")
    if phone_value is None:
        phone_value = mapped_data.get("phone") or mapped_data.get("phones")
    if isinstance(phone_value, dict):
        primary_phone = phone_value.get("primaryPhoneNumber") or phone_value.get("value") or phone_value.get("number") or ""
        additional_phones = phone_value.get("additionalPhones") or []
    elif isinstance(phone_value, list):
        primary_phone = str(phone_value[0]).strip() if phone_value else ""
        additional_phones = [str(item).strip() for item in phone_value[1:] if str(item).strip()]
    elif isinstance(phone_value, str):
        primary_phone = phone_value.strip()
        additional_phones = []
    else:
        primary_phone = ""
        additional_phones = []
    if primary_phone or additional_phones:
        cleaned["phones"] = {
            "primaryPhoneNumber": str(primary_phone).strip(),
            "primaryPhoneCountryCode": "",
            "primaryPhoneCallingCode": "",
            "additionalPhones": [str(item).strip() for item in additional_phones if str(item).strip()],
        }

    company = mapped_data.get("company") or mapped_data.get("company_name") or mapped_data.get("companyName")
    if company:
        cleaned["companyName"] = str(company).strip()

    if "more_info" in mapped_data and mapped_data.get("more_info"):
        cleaned["jobTitle"] = str(mapped_data["more_info"]).strip()

    for key, value in mapped_data.items():
        if value is None:
            continue
        if isinstance(key, str) and key.lower() in {"crm_destination", "sync_status", "date_sent_to_crm", "lifecycle_stage"}:
            cleaned[key] = value

    return cleaned


def _is_placeholder_url(url: str) -> bool:
    if not isinstance(url, str):
        return True
    candidate = url.strip()
    if not candidate:
        return True
    try:
        parsed = __import__("urllib.parse").urlparse(candidate)
    except Exception:
        return True

    hostname = (parsed.netloc or parsed.path or "").split(":")[0].lower()
    if not hostname:
        return True

    blocked = {
        "example.com",
        "www.example.com",
        "api.example.com",
        "example.org",
        "example.net",
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
    }
    if hostname in blocked or hostname.endswith(".example.com") or hostname.endswith(".localhost"):
        return True
    if any(token in hostname for token in ("example", "dummy", "placeholder", "sample", "testdomain")):
        return True
    return False


def _extract_ddgs_endpoint_candidates(results: list) -> list:
    urls = []
    for item in results or []:
        combined = " ".join([
            str(item.get("title") or ""),
            str(item.get("body") or ""),
            str(item.get("href") or ""),
        ])
        for match in re.findall(r"https?://[^\s\'\"<>]+", combined):
            clean = match.rstrip("),.;]")
            if clean and not _is_placeholder_url(clean):
                urls.append(clean)
    return urls


def _resolve_ddgs_endpoint(crm_name: str, target: str | None = None) -> str | None:
    query_variants = [
        f"{crm_name} CRM API create lead endpoint documentation",
        f"{crm_name} API create contact endpoint docs",
        f"{crm_name} REST API create person endpoint docs",
        f"{crm_name} developers API create record endpoint",
    ]
    seen = set()
    for query in query_variants:
        try:
            with DDGS() as ddgs:
                results = ddgs.text(query, max_results=6)
        except Exception:
            continue
        candidates = _extract_ddgs_endpoint_candidates(results)
        for candidate in candidates:
            if candidate in seen or _is_placeholder_url(candidate):
                continue
            seen.add(candidate)
            parsed = __import__("urllib.parse").urlparse(candidate)
            hostname = parsed.netloc.lower()
            crm_key = crm_name.lower().replace(" ", "")
            if crm_key in hostname.replace(".", ""):
                return candidate
            if hostname.startswith("api.") or ".api." in hostname:
                return candidate
            return candidate
    return None


def _resolve_ddgs_endpoints_for_retry(crm_name: str, previous_error: str | None = None) -> tuple[list, list]:
    query_variants = [
        f"{crm_name} CRM API create contact endpoint documentation",
        f"{crm_name} REST API create lead endpoint docs",
        f"{crm_name} people API create record documentation",
    ]
    if previous_error:
        cleaned = re.sub(r"\s+", " ", previous_error).strip()
        if len(cleaned) > 30:
            query_variants.insert(0, f"{crm_name} {cleaned[:120]} API docs")

    candidates = []
    snippets = []
    seen_urls = set()
    for query in query_variants:
        try:
            with DDGS() as ddgs:
                results = ddgs.text(query, max_results=5)
        except Exception:
            continue
        for item in results or []:
            title = str(item.get("title") or "")
            body = str(item.get("body") or "")
            snippet = f"{title}: {body}".strip()
            if snippet:
                snippets.append(snippet)
            for candidate in _extract_ddgs_endpoint_candidates([item]):
                if candidate not in seen_urls and not _is_placeholder_url(candidate):
                    seen_urls.add(candidate)
                    candidates.append(candidate)
    return candidates, snippets


def _choose_best_endpoint_from_candidates(crm_name: str, candidates: list[str]) -> str | None:
    if not candidates:
        return None
    crm_key = crm_name.lower().replace(" ", "")
    for candidate in candidates:
        hostname = __import__("urllib.parse").urlparse(candidate).netloc.lower()
        if crm_key in hostname.replace(".", ""):
            return candidate
    for candidate in candidates:
        hostname = __import__("urllib.parse").urlparse(candidate).netloc.lower()
        if hostname.startswith("api.") or ".api." in hostname:
            return candidate
    return candidates[0]


def _finalize_url_for_crm(crm_name: str, proposed_url: str | None, user_provided_target: str = "") -> str:
    normalized = crm_name.lower().strip()
    if normalized == "hubspot":
        return HUBSPOT_CONTACTS_URL
    if normalized == "twenty":
        return "https://api.twenty.com/rest/people"
    if not proposed_url:
        return user_provided_target
    if _is_placeholder_url(proposed_url):
        discovered_candidates, _ = _resolve_ddgs_endpoints_for_retry(crm_name, proposed_url)
        discovered = _choose_best_endpoint_from_candidates(crm_name, discovered_candidates)
        if discovered:
            return discovered
        discovered = _resolve_ddgs_endpoint(crm_name, user_provided_target)
        if discovered:
            return discovered
        raise ValueError(f"No valid endpoint could be found for '{crm_name}' via the web search results. Refusing to call an example/placeholder URL.")
    return proposed_url


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

    if normalized == "twenty":
        url = "https://api.twenty.com/rest/people"
        headers = {"Content-Type": "application/json"}
        if user_provided_target and (user_provided_target.startswith("http://") or user_provided_target.startswith("https://")):
            if _is_placeholder_url(user_provided_target):
                raise ValueError(f"Invalid Twenty endpoint: {user_provided_target}")
            url = user_provided_target
        if user_provided_target and not (user_provided_target.startswith("http://") or user_provided_target.startswith("https://")):
            headers["Authorization"] = f"Bearer {user_provided_target}"
        return url, headers, _normalize_twenty_payload(mapped_data)

    if user_provided_target.startswith("http://") or user_provided_target.startswith("https://"):
        if _is_placeholder_url(user_provided_target):
            raise ValueError(f"Refusing non-production endpoint: {user_provided_target}")
        return user_provided_target, {"Content-Type": "application/json"}, mapped_data

    return user_provided_target, {"Content-Type": "application/json"}, mapped_data


def _normalize_airtable_field_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name).lower())


def _apply_airtable_field_schema(mapped_data: dict) -> dict:
    if not isinstance(mapped_data, dict):
        return mapped_data

    env_schema = ""
    for env_name in ("AIRTABLE_FIELD_NAMES", "AIRTABLE_FIELDS", "AIRTABLE_SCHEMA", "AIRTABLE_TABLE_SCHEMA"):
        candidate = os.getenv(env_name)
        if candidate and candidate.strip():
            env_schema = candidate.strip()
            break

    if not env_schema:
        return mapped_data

    field_names = [
        part.strip()
        for part in re.split(r"[\n,;]+", env_schema)
        if part and part.strip()
    ]
    if not field_names:
        return mapped_data

    field_lookup = {
        _normalize_airtable_field_name(field_name): field_name
        for field_name in field_names
    }

    remapped = {}
    for key, value in mapped_data.items():
        canonical_key = _normalize_airtable_field_name(key)
        remapped[field_lookup.get(canonical_key, str(key))] = value
    return remapped


def push_to_airtable(mapped_data: dict) -> dict:
    api_key = os.getenv("AIRTABLE_API_KEY")
    base_id = os.getenv("AIRTABLE_BASE_ID")
    table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")

    if not api_key or not base_id:
        raise ValueError("Airtable requires AIRTABLE_API_KEY and AIRTABLE_BASE_ID in the environment.")

    payload = {"fields": _apply_airtable_field_schema(mapped_data)}
    url = f"https://api.airtable.com/v0/{base_id}/{table_name}"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json=payload, timeout=10)
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
        if normalized_crm in {"hubspot", "twenty"}:
            url, headers, payload = _resolve_known_crm_request(normalized_crm, user_provided_target, mapped_data)
            trajectory.append({"step": "Canonical CRM Route", "url": url, "detail": f"Using the known-good {normalized_crm} endpoint."})
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
                            results = ddgs.text(f"{crm_name} CRM API create contact endpoint documentation", max_results=5)
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
                1. If 'Target' is a URL (starts with http), use it as 'url' only if it is not a placeholder/example domain.
                2. If 'Target' is a token, look at the directory to find the API 'url'.
                3. Formulate correct 'headers' (e.g., Authorization: Bearer <token>).
                4. Structure 'payload' properly, omitting null values.
                5. Never invent example.com, localhost, or placeholder domains.
                6. For HubSpot, the only valid URL is https://api.hubapi.com/crm/v3/objects/contacts.
                7. For Twenty, the valid URL is https://api.twenty.com/rest/people.
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

                if isinstance(url, str) and _is_placeholder_url(url):
                    discovered_candidates, discovered_snippets = _resolve_ddgs_endpoints_for_retry(crm_name, api_response_text if 'api_response_text' in locals() else None)
                    discovered = _choose_best_endpoint_from_candidates(crm_name, discovered_candidates)
                    if discovered:
                        url = discovered
                        trajectory.append({"step": "Search-Based Endpoint Recovery", "url": url, "detail": f"Replaced placeholder endpoint with a verified DDGS result for {crm_name}."})
                    else:
                        trajectory.append({
                            "step": "Doc Search Pending",
                            "detail": f"No valid endpoint found for {crm_name} yet; retry loop will refresh docs and try a new route.",
                            "snippets": discovered_snippets[:3],
                        })

                trajectory.append({"step": "LLM Initial Request Formulation", "url": url, "payload": payload})
                save_memory(crm_name, input_keys_signature, json.dumps(payload))

                if normalized_crm == "hubspot":
                    url, headers, payload = _resolve_known_crm_request(normalized_crm, user_provided_target, mapped_data)
                    trajectory.append({"step": "HubSpot Route Override", "url": url, "detail": "Overrode model-generated endpoint to the official HubSpot API."})
                elif normalized_crm == "twenty":
                    url, headers, payload = _resolve_known_crm_request(normalized_crm, user_provided_target, mapped_data)
                    trajectory.append({"step": "Twenty Payload Normalization", "url": url, "detail": "Removed unsupported flat fields and converted them to Twenty-compatible payload keys."})

            if normalized_crm == "hubspot" and cached_mapping:
                url, headers, payload = _resolve_known_crm_request(normalized_crm, user_provided_target, mapped_data)
                trajectory.append({"step": "HubSpot Memory Rebuild", "url": url, "detail": "Rebuilt the HubSpot request using the canonical endpoint and PAT auth."})

    # Execution & Self-Correction Loop
    max_retries = 5
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
            elif normalized_crm not in {"hubspot", "twenty"}:
                refreshed_candidates, refreshed_snippets = _resolve_ddgs_endpoints_for_retry(normalized_crm, readable_error)
                if refreshed_candidates:
                    refreshed_url = _choose_best_endpoint_from_candidates(normalized_crm, refreshed_candidates)
                    if refreshed_url:
                        trajectory.append({
                            "step": "Doc Refresh for Retry",
                            "attempt": attempt + 1,
                            "url": refreshed_url,
                            "source": refreshed_snippets[:2],
                        })
                        url = refreshed_url
                payload, removed_fields = _remove_rejected_fields(payload, api_response.text)
                if removed_fields:
                    trajectory.append({
                        "step": "Deterministic Field Removal",
                        "removed_fields": sorted(removed_fields),
                    })
            else:
                payload, removed_fields = _remove_rejected_fields(payload, api_response.text)
                if removed_fields:
                    trajectory.append({
                        "step": "Deterministic Field Removal",
                        "removed_fields": sorted(removed_fields),
                    })
            correction_prompt = f"""
            API request failed with status {api_response.status_code}.
            Human-readable error: {readable_error}
            Technical API response: {api_response.text}
            Current payload:
            {json.dumps(payload)}
            Documentation search results for this CRM:
            {json.dumps(_resolve_ddgs_endpoints_for_retry(normalized_crm, readable_error)[1][:5]) if normalized_crm not in {'hubspot', 'twenty'} else '[]'}
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
                if normalized_crm not in {"hubspot", "twenty"}:
                    refreshed_candidates, refreshed_snippets = _resolve_ddgs_endpoints_for_retry(normalized_crm, readable_error)
                    discovered = _choose_best_endpoint_from_candidates(normalized_crm, refreshed_candidates)
                    if discovered:
                        url = discovered
                        trajectory.append({
                            "step": "Retry Refresh from Docs",
                            "url": url,
                            "detail": refreshed_snippets[:2],
                        })
                    corrected_url = corrected.get("url", url)
                    if not isinstance(corrected_url, str) or _is_placeholder_url(corrected_url):
                        corrected["url"] = url
                try:
                    url = _finalize_url_for_crm(normalized_crm, corrected.get("url", url), user_provided_target)
                except ValueError:
                    if normalized_crm not in {"hubspot", "twenty"}:
                        refreshed_candidates, refreshed_snippets = _resolve_ddgs_endpoints_for_retry(normalized_crm, readable_error)
                        discovered = _choose_best_endpoint_from_candidates(normalized_crm, refreshed_candidates)
                        if discovered:
                            url = discovered
                            trajectory.append({
                                "step": "Retry Refresh from Docs",
                                "url": url,
                                "detail": refreshed_snippets[:2],
                            })
                        else:
                            if attempt == max_retries - 1:
                                raise
                            continue
                    else:
                        raise
                headers = corrected.get("headers", headers) or {}
                payload = corrected.get("payload", payload)
                if normalized_crm == "twenty":
                    payload = _normalize_twenty_payload(payload)
                payload, removed_fields = _remove_rejected_fields(payload, api_response.text)
                if removed_fields:
                    trajectory.append({
                        "step": "Rejected Field Cleanup",
                        "removed_fields": sorted(removed_fields),
                    })
                if normalized_crm == "twenty" and user_provided_target and not (
                    user_provided_target.startswith("http://") or user_provided_target.startswith("https://")
                ):
                    token = user_provided_target.strip()
                    headers = {**headers, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                else:
                    headers = {**headers, "Content-Type": "application/json"}
        else:
            readable_error = _format_destination_error(normalized_crm, api_response.status_code, api_response.text)
            trajectory.append({"step": "Execution Failed", "error": readable_error, "response": api_response.text})
            _save_trajectory(trajectory, trajectory_id)
            raise Exception(f"Agent exhausted retries. {readable_error}")
            
    return {}, trajectory