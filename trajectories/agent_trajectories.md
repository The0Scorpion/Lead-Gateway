# Agent Trajectories

This document contains representative trajectories for the two autonomous agents used in the Agentic Lead Gateway, demonstrating their instructions, tool usage, feedback loops, and retries.

## Trajectory 1: The Onboarding Agent (Tool Usage & Inference)

This agent is responsible for dynamically generating UI instructions and inferring the standard contact schema for unknown CRMs.

* **Agent Goal:** Provide accurate authentication steps and schema fields for an unknown CRM ("Pipedrive").
* **System Instructions:** "You are an expert CRM Integration Engineer. The user wants to integrate 'Pipedrive'. Using the live search results... provide concise, numbered, UI-specific steps... and infer a list of standard string field names."
* **Tool Invocation:** The agent triggers `search_crm_onboarding_docs("Pipedrive")`.
* **Tool Response (DuckDuckGo Search):** 
  ```text
  Auth Source 1: How to find your personal API key - Pipedrive
  Details: To find your personal API key, go to Settings > Personal preferences > API. Click 'Generate new token' if one does not exist...
  
  Schema Source 1: Pipedrive API v1 Reference - Persons
  Details: A person object can contain fields such as name, first_name, last_name, phone, email, and org_id...

```

* **Final Result (Agent Output):**
```json
{
  "instructions": "1. Log in to your Pipedrive account.\n2. Go to Settings > Personal preferences > API.\n3. Copy your personal API key and paste it below.",
  "expected_fields": ["first_name", "last_name", "phone", "email", "org_id"]
}

```



---

## Trajectory 2: The Dispatch Agent (Self-Correction Loop & API Routing)

This agent is responsible for structuring the HTTP request, mapping messy incoming fields, and self-correcting if the destination API rejects the payload.

* **Agent Goal:** Map a messy JSON payload to HubSpot's required format and successfully POST it.
* **System Instructions:** "You are an autonomous API routing agent. Formulate the exact HTTP POST request needed to create a contact/lead in the specified CRM. Output ONLY a valid JSON object containing 'url', 'headers', and 'payload'."
* **User Input (Messy Payload):**
```json
{
  "seller_first_name": "John",
  "contact_cell": "555-019-8822",
  "email": "None" 
}

```


* **Step 1 (Initial Generation):** The agent reads the injected API Directory and formulates the first request configuration.
```json
{
  "url": "[https://api.hubapi.com/crm/v3/objects/contacts](https://api.hubapi.com/crm/v3/objects/contacts)",
  "headers": {"Authorization": "Bearer pat-na1-12345"},
  "payload": {
    "properties": {
      "firstname": "John",
      "phone": "555-019-8822",
      "email": "None"
    }
  }
}

```


* **Environment Feedback (Execution Result):** The POST request is executed. HubSpot returns a `400 Bad Request` validation error.
```text
HTTP Status: 400
Response: {"status":"error","message":"Property values were not valid: [{\"isValid\":false,\"message\":\"Email address None is invalid\",\"error\":\"INVALID_EMAIL\",\"name\":\"email\"}]"}

```


* **Retry / Feedback Loop:** The environment feeds this exact 400 error and the previous request config back to the agent with the instruction: *"Analyze the error... If it is a 400 validation error, fix the 'payload' (e.g., remove invalid emails, fix nested objects)."*
* **Final Result (Corrected Output):** The agent self-corrects by stripping the invalid email key entirely, ensuring the payload fits HubSpot's strict validation rules.
```json
{
  "url": "[https://api.hubapi.com/crm/v3/objects/contacts](https://api.hubapi.com/crm/v3/objects/contacts)",
  "headers": {"Authorization": "Bearer pat-na1-12345"},
  "payload": {
    "properties": {
      "firstname": "John",
      "phone": "555-019-8822"
    }
  }
}

```


* **Final Execution:** The corrected request config is executed, and the environment returns `201 Created`. The trajectory is successfully completed.
