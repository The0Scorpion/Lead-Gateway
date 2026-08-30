# db.py
import sqlite3
import os
import json
import ast
import uuid
DB_FILE = "routes.db"

def _decode_payload(payload_text):
    try:
        return json.loads(payload_text)
    except Exception:
        try:
            return ast.literal_eval(payload_text)
        except Exception:
            return {}

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 1. Routes Table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS routes (
            lead_key TEXT PRIMARY KEY,
            client_name TEXT NOT NULL,
            crm_name TEXT NOT NULL,
            webhook_url TEXT NOT NULL,
            target_fields TEXT
        )
    ''')
    
    # Self-healing migration: Automatically fix missing columns or old schema names
    cursor.execute("PRAGMA table_info(routes)")
    columns = [col[1] for col in cursor.fetchall()]
    if "crm_type" in columns and "crm_name" not in columns:
        cursor.execute("ALTER TABLE routes RENAME COLUMN crm_type TO crm_name")
    elif "crm_name" not in columns:
        cursor.execute("ALTER TABLE routes ADD COLUMN crm_name TEXT NOT NULL DEFAULT 'hubspot'")

    # 2. Agent Memory Table (Caching successful field transformations)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS agent_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crm_name TEXT,
            input_signature TEXT UNIQUE,
            mapped_result TEXT
        )
    ''')

    # 3. Quarantine / Review Queue Table (Human-in-the-loop)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quarantined_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_key TEXT,
            client_name TEXT,
            crm_name TEXT,
            payload TEXT,
            error_message TEXT,
            status TEXT DEFAULT 'pending'
        )
    ''')
    
    conn.commit()
    conn.close()

def add_route(client_name, crm_name=None, crm_type=None, webhook_url="", target_fields=None):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # Automatically accept whichever field main.py passes (crm_name or crm_type)
    resolved_crm = crm_name or crm_type or "hubspot"
    
    # Auto-generate a clean, unique lead key (e.g., "acme-corp-a1b2")
    safe_name = "".join(c for c in client_name.lower() if c.isalnum() or c == "-").strip("-")
    lead_key = f"{safe_name}-{str(uuid.uuid4())[:4]}"
    
    # Serialize the list to JSON string safely (handling empty lists)
    target_fields_json = json.dumps(target_fields or [])
    
    cursor.execute('''
        INSERT OR REPLACE INTO routes (lead_key, client_name, crm_name, webhook_url, target_fields)
        VALUES (?, ?, ?, ?, ?)
    ''', (lead_key, client_name, resolved_crm, webhook_url, target_fields_json))
    
    conn.commit()
    conn.close()
    
    # Return the generated key back to main.py
    return lead_key

def get_route(lead_key):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT lead_key, client_name, crm_name, webhook_url, target_fields FROM routes WHERE lead_key = ?", (lead_key,))
    row = cursor.fetchone()
    conn.close()
    if row:
        # Safely convert the JSON string back into a Python list
        try:
            target_fields = json.loads(row[4]) if row[4] else []
        except Exception:
            target_fields = []

        return {
            "lead_key": row[0],
            "client_name": row[1],
            "crm_name": row[2],
            "crm_type": row[2],
            "webhook_url": row[3],
            "target_fields": target_fields
        }
    return None

def get_all_routes():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT lead_key, client_name, crm_name, webhook_url FROM routes")
    rows = cursor.fetchall()
    conn.close()
    return [{"lead_key": r[0], "client_name": r[1], "crm": r[2], "webhook_url": r[3]} for r in rows]

def delete_route(lead_key):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM routes WHERE lead_key = ?", (lead_key,))
    deleted = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return deleted

# --- Memory & Quarantine Helpers ---
def get_memory(crm_name: str, input_signature: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT mapped_result FROM agent_memory WHERE crm_name = ? AND input_signature = ?", (crm_name, input_signature))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

def save_memory(crm_name: str, input_signature: str, mapped_result: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO agent_memory (crm_name, input_signature, mapped_result) VALUES (?, ?, ?)", 
                   (crm_name, input_signature, mapped_result))
    conn.commit()
    conn.close()

def add_to_quarantine(lead_key, client_name, crm_name, payload, error_message):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO quarantined_leads (lead_key, client_name, crm_name, payload, error_message, status)
        VALUES (?, ?, ?, ?, ?, 'pending')
    ''', (lead_key, client_name, crm_name, json.dumps(payload), error_message))
    conn.commit()
    conn.close()

def get_quarantined_leads():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT id, lead_key, client_name, crm_name, payload, error_message FROM quarantined_leads WHERE status = 'pending'")
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        payload = json.dumps(_decode_payload(row[4]), indent=2)
        result.append({"id": row[0], "lead_key": row[1], "client_name": row[2], "crm_name": row[3], "payload": payload, "error_message": row[5]})
    return result

def get_quarantined_lead(qid: int):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, lead_key, client_name, crm_name, payload, error_message, status FROM quarantined_leads WHERE id = ?",
        (qid,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    payload = _decode_payload(row[4])
    return {
        "id": row[0],
        "lead_key": row[1],
        "client_name": row[2],
        "crm_name": row[3],
        "payload": payload,
        "error_message": row[5],
        "status": row[6],
    }

def update_quarantine(qid: int, payload, error_message: str, status: str = "pending"):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE quarantined_leads SET payload = ?, error_message = ?, status = ? WHERE id = ?",
        (json.dumps(payload), error_message, status, qid),
    )
    conn.commit()
    conn.close()

def resolve_quarantine(qid: int, status: str):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE quarantined_leads SET status = ? WHERE id = ?", (status, qid))
    conn.commit()
    conn.close()