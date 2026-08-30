# agent.py
import os
import json
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# Point the standard OpenAI client directly at Ollama's local server
client = OpenAI(
    base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
    api_key=os.getenv("OLLAMA_API_KEY", "ollama")
)

MODEL_NAME = os.getenv("OLLAMA_MODEL", "llama3.2")


def _sanitize_mapped_lead(mapped_data: dict) -> dict:
    missing_values = {"", "none", "null", "n/a", "na"}
    sanitized = {}
    for key, value in (mapped_data or {}).items():
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if value.lower() in missing_values:
                continue
            if key.lower() in {"email", "email_address"} and "@" not in value:
                continue
        sanitized[key] = value
    return sanitized

def map_and_enrich_lead(raw_payload: dict, target_fields: list) -> dict:
    system_prompt = (
        "You are an autonomous real estate data routing engine. "
        "Your task is to take arbitrary incoming lead JSON data and normalize it "
        "into an exact target schema required by the destination CRM. "
        "Always output ONLY a valid JSON object with keys matching the target fields."
    )

    # agent.py (Excerpt)

    user_prompt = f"""
Target CRM Schema Required:
{json.dumps(target_fields, indent=2)}

Raw Incoming Lead Payload:
{json.dumps(raw_payload, indent=2)}

Mapping & Normalization Rules:
1. Map corresponding semantic values from the raw payload into the Target CRM Schema.
2. If first and last names are split, merge them into the appropriate full name field (or split them if the schema requires).
3. Standardize phone numbers into a standard readable format (e.g., (XXX) XXX-XXXX or E.164).
4. Analyze the distress level or notes to infer an urgency/priority value ("HIGH", "MEDIUM", or "LOW").
5. Omit any value that is null, empty, "None", "null", "N/A", or "NA" (case-insensitive).
6. Omit an email unless it is a plausible address containing both "@" and a domain portion.
7. Aggregate extra information into a catch-all field only when that field is explicitly present in the Target CRM Schema. Never invent fields such as "notes".
8. Return ONLY a single JSON object where every key is one of the requested target fields and every value is valid and non-empty.
"""

    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.0,
        response_format={"type": "json_object"}
    )

    raw_response = response.choices[0].message.content.strip()
    return _sanitize_mapped_lead(json.loads(raw_response))