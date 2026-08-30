# main.py
import json
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from contextlib import asynccontextmanager

from db import init_db, get_route, add_route, get_all_routes, delete_route, add_to_quarantine, get_quarantined_leads, get_quarantined_lead, update_quarantine, resolve_quarantine, save_memory
from agent_helper import get_crm_integration_details
from agent import map_and_enrich_lead
from dispatch import push_to_airtable, dispatch_agentic, _hubspot_payload, format_stored_error

# Initialize the SQLite database on startup
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield

app = FastAPI(
    title="Agentic Universal Lead Gateway",
    lifespan=lifespan
)

# --- REQUEST MODELS ---
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return Response(status_code=204)  # 204 No Content
class CRMRequest(BaseModel):
    crm_name: str

class CreateRouteRequest(BaseModel):
    client_name: str
    crm_name: str
    webhook_url: str
    target_fields: list

class QuarantineResendRequest(BaseModel):
    payload: dict

# --- CORE INGESTION & DISPATCH ROUTE ---

@app.post("/api/v1/lead/{lead_key}")
async def ingest_lead(lead_key: str, request: Request):
    # 1. Resolve Lead Route
    route = get_route(lead_key)
    if not route:
        raise HTTPException(
            status_code=404,
            detail=f"Route for Lead Key '{lead_key}' not found."
        )

    # 2. Extract Inbound JSON
    try:
        raw_payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="Invalid request body. Valid JSON is required."
        )

    # 3. Agentic Normalization via Local Ollama Model
    try:
        mapped_data = map_and_enrich_lead(
            raw_payload=raw_payload,
            target_fields=route["target_fields"]
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Agent schema mapping failed: {str(e)}"
        )

    # 4. Dispatch to Destination CRM / Webhook
    crm_type = str(route.get("crm_type") or "").strip().lower()
    destination_target = str(route.get("webhook_url") or "").strip()
    dispatch_result = {}

    try:
        # Airtable remains hardcoded only because it uses backend .env vars (Base ID/Table Name)
        if crm_type == "airtable":
            dispatch_result = push_to_airtable(mapped_data)
        else:
            # Scalable Agentic Fallback: Automatically handles HubSpot, Webhooks, GoHighLevel, etc.
            dispatch_result = dispatch_agentic(
                crm_name=crm_type, 
                user_provided_target=destination_target, 
                mapped_data=mapped_data,
                trajectory_id=lead_key,
            )
        return {
            "status": "success",
            "lead_key": lead_key,
            "dispatch_response": dispatch_result[0] if isinstance(dispatch_result, tuple) else dispatch_result,
            "agent_trajectory": dispatch_result[1] if isinstance(dispatch_result, tuple) else [],
        }
    except Exception as e:
        print(f"\n[DISPATCH ERROR]: {str(e)}\n")
        add_to_quarantine(
            lead_key=lead_key,
            client_name=route["client_name"],
            crm_name=crm_type,
            payload=mapped_data,
            error_message=str(e),
        )
        raise HTTPException(
            status_code=502,
            detail=f"Destination dispatch error: {str(e)}"
        )

# --- AGENT ONBOARDING & DASHBOARD ROUTES ---
@app.delete("/api/v1/routes/{lead_key}")
async def remove_route(lead_key: str):
    success = delete_route(lead_key)
    if not success:
        raise HTTPException(status_code=404, detail=f"Route '{lead_key}' not found.")
    return {"status": "success", "message": f"Route '{lead_key}' deleted successfully."}
@app.get("/api/v1/quarantine")
async def list_quarantine():
    leads = get_quarantined_leads()
    for lead in leads:
        lead["error_message"] = format_stored_error(lead["crm_name"], lead["error_message"])
    return {"quarantined_leads": leads}

@app.post("/api/v1/quarantine/{qid}/resolve")
async def resolve_quarantine_lead(qid: int, action: dict):
    """Action format: {'status': 'approved' or 'rejected'}"""
    status = action.get("status", "rejected")
    resolve_quarantine(qid, status)
    return {"status": "success", "message": f"Quarantine lead {qid} marked as {status}"}

@app.post("/api/v1/quarantine/{qid}/resend")
async def resend_quarantined_lead(qid: int, request: QuarantineResendRequest):
    quarantined = get_quarantined_lead(qid)
    if not quarantined:
        raise HTTPException(status_code=404, detail="Quarantined lead not found.")
    if quarantined["status"] != "pending":
        raise HTTPException(status_code=409, detail="Only pending quarantined leads can be resent.")

    route = get_route(quarantined["lead_key"])
    if not route:
        raise HTTPException(status_code=404, detail="Route for quarantined lead no longer exists.")

    payload = request.payload
    crm_type = str(route.get("crm_type") or "").strip().lower()
    destination_target = str(route.get("webhook_url") or "").strip()
    try:
        if crm_type == "airtable":
            dispatch_result = push_to_airtable(payload)
        else:
            dispatch_result = dispatch_agentic(crm_type, destination_target, payload, quarantined["lead_key"])
        memory_payload = payload
        if crm_type == "hubspot":
            memory_payload = _hubspot_payload(payload)["properties"]
        signature = "-".join(sorted(memory_payload.keys()))
        save_memory(crm_type, signature, json.dumps(memory_payload))
        update_quarantine(qid, payload, "Resolved and resent successfully.", "approved")
        return {"status": "success", "dispatch_response": dispatch_result}
    except Exception as e:
        update_quarantine(qid, payload, str(e), "pending")
        raise HTTPException(status_code=502, detail=f"Resend failed: {str(e)}")
@app.post("/api/v1/agent/crm-info")
async def fetch_crm_instructions(request: CRMRequest):
    try:
        details = get_crm_integration_details(request.crm_name)
        return {"status": "success", "data": details}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/v1/routes")
async def create_new_route(request: CreateRouteRequest):
    try:
        lead_key = add_route(
            client_name=request.client_name,
            crm_type=request.crm_name,
            webhook_url=request.webhook_url,
            target_fields=request.target_fields
        )
        return {"status": "success", "lead_key": lead_key}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/dashboard/stats")
async def get_dashboard_stats():
    return {"active_routes": get_all_routes()}

# --- STATIC FILES & FRONTEND ---

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def serve_dashboard():
    return FileResponse("static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)