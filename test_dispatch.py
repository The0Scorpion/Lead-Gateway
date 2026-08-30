from types import SimpleNamespace

import dispatch


def test_airtable_uses_env_schema_when_present(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json

        class FakeResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {"id": "air-123"}

        return FakeResponse()

    monkeypatch.setenv("AIRTABLE_API_KEY", "pat-test")
    monkeypatch.setenv("AIRTABLE_BASE_ID", "app-test")
    monkeypatch.setenv("AIRTABLE_TABLE_NAME", "Leads")
    monkeypatch.setenv(
        "AIRTABLE_FIELD_NAMES",
        "First Name,Last Name,Phone,Email,Address,Lifecycle Stage,CRM Destination,Sync Status,Date Sent to CRM,More Info",
    )
    monkeypatch.setattr(dispatch.requests, "post", fake_post)

    result = dispatch.push_to_airtable(
        {
            "first_name": "Alice",
            "last_name": "Smith",
            "phone": "555-111-2222",
            "email": "alice@example.com",
            "address": "123 Main St",
            "lifecycle_stage": "Qualified",
            "crm_destination": "HubSpot",
            "sync_status": "Synced",
            "date_sent_to_crm": "2026-08-30",
            "more_info": "VIP client",
        }
    )

    assert captured["payload"] == {
        "fields": {
            "First Name": "Alice",
            "Last Name": "Smith",
            "Phone": "555-111-2222",
            "Email": "alice@example.com",
            "Address": "123 Main St",
            "Lifecycle Stage": "Qualified",
            "CRM Destination": "HubSpot",
            "Sync Status": "Synced",
            "Date Sent to CRM": "2026-08-30",
            "More Info": "VIP client",
        }
    }
    assert result["id"] == "air-123"


def test_twenty_dispatch_rejects_example_url_and_forces_canonical_endpoint(monkeypatch):
    captured = {}

    def fake_create(*args, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"url": "https://api.example.com/submit_lead", "headers": {"Content-Type": "application/json"}, "payload": {"first_name": "Test"}}'
                    )
                )
            ]
        )

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json

        class FakeResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {"id": "twenty-123"}

        return FakeResponse()

    monkeypatch.setattr(dispatch, "get_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatch, "save_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatch.client, "chat", SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(dispatch.requests, "post", fake_post)

    result, trajectory = dispatch.dispatch_agentic(
        crm_name="twenty",
        user_provided_target="secret-token",
        mapped_data={"first_name": "Test", "last_name": "User"},
    )

    assert captured["url"] == "https://api.twenty.com/rest/people"
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert result["id"] == "twenty-123"
    assert any(step.get("step") == "Dispatch Success" for step in trajectory)


def test_twenty_retry_keeps_bearer_token(monkeypatch):
    captured = {"calls": []}
    create_calls = {"count": 0}

    def fake_create(*args, **kwargs):
        create_calls["count"] += 1
        if create_calls["count"] == 1:
            content = '{"url": "https://api.twenty.com/rest/people", "headers": {"Content-Type": "application/json"}, "payload": {"first_name": "Test", "phone": "555-123-4567"}}'
        else:
            content = '{"url": "https://api.twenty.com/rest/people", "headers": {"Content-Type": "application/json"}, "payload": {"first_name": "Test"}}'
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["calls"].append({"url": url, "headers": dict(headers or {}), "payload": json})
        if len(captured["calls"]) == 1:
            class FakeResponse:
                status_code = 403
                text = '{"statusCode":403,"messages":["Missing authentication token"],"error":"FORBIDDEN_EXCEPTION"}'

                def json(self):
                    return {"error": "FORBIDDEN_EXCEPTION"}

            return FakeResponse()

        class FakeResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {"id": "twenty-retry-123"}

        return FakeResponse()

    monkeypatch.setattr(dispatch, "get_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatch, "save_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatch.client, "chat", SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(dispatch.requests, "post", fake_post)

    result, _ = dispatch.dispatch_agentic(
        crm_name="twenty",
        user_provided_target="secret-token",
        mapped_data={"first_name": "Test", "last_name": "User", "phone": "555-123-4567"},
    )

    assert captured["calls"][1]["headers"]["Authorization"] == "Bearer secret-token"
    assert "phone" not in captured["calls"][1]["payload"]
    assert result["id"] == "twenty-retry-123"


def test_unknown_crm_retries_with_new_docs_and_five_attempts(monkeypatch):
    captured = {"calls": [], "ddgs_queries": []}
    docs = [
        ["https://api.example.com/submit_lead"],
        ["https://api.acmecrm.com/v1/people"],
    ]

    class FakeDDGS:
        def __init__(self, *args, **kwargs):
            self._turn = 0

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def text(self, query, max_results=5):
            captured["ddgs_queries"].append(query)
            lower = str(query).lower()
            if "create contact endpoint documentation" in lower or "create lead endpoint documentation" in lower:
                return [{"title": "Doc", "body": "lead endpoint", "href": "https://api.example.com/submit_lead"}]
            return [{"title": "Doc", "body": "lead endpoint", "href": "https://api.acmecrm.com/v1/people"}]

    def fake_create(*args, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"url": "https://api.example.com/submit_lead", "headers": {"Content-Type": "application/json"}, "payload": {"first_name": "Test"}}'
                    )
                )
            ]
        )

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["calls"].append({"url": url, "headers": dict(headers or {}), "payload": json})
        if len(captured["calls"]) < 3:
            class FakeResponse:
                status_code = 400
                text = '{"message":"Object person does not have any \"phone\" field."}'

                def json(self):
                    return {"message": "Object person does not have any \"phone\" field."}

            return FakeResponse()

        class FakeResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {"id": "acme-123"}

        return FakeResponse()

    monkeypatch.setattr(dispatch, "get_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatch, "save_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatch, "DDGS", FakeDDGS)
    monkeypatch.setattr(dispatch.client, "chat", SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(dispatch.requests, "post", fake_post)

    result, trajectory = dispatch.dispatch_agentic(
        crm_name="acmecrm",
        user_provided_target="token-123",
        mapped_data={"first_name": "Test", "last_name": "User", "phone": "555-123-4567"},
    )

    assert result["id"] == "acme-123"
    assert len(captured["calls"]) >= 3
    assert len(captured["ddgs_queries"]) >= 2
    assert any(step.get("step") == "Dispatch Success" for step in trajectory)


def test_twenty_normalizes_name_email_and_phone_objects():
    payload = dispatch._normalize_twenty_payload(
        {
            "first_name": "Marcus",
            "last_name": "Vance",
            "phone": "555-111-2222",
            "email": "marcus@example.com",
            "address": "123 Main St",
        }
    )

    assert payload["name"] == {"firstName": "Marcus", "lastName": "Vance"}
    assert payload["emails"] == {"primaryEmail": "marcus@example.com", "additionalEmails": []}
    assert payload["phones"] == {
        "primaryPhoneNumber": "555-111-2222",
        "primaryPhoneCountryCode": "",
        "primaryPhoneCallingCode": "",
        "additionalPhones": [],
    }
    assert "address" not in payload


def test_twenty_retry_preserves_values_when_model_returns_shape_incorrect(monkeypatch):
    captured = {"calls": []}
    create_calls = {"count": 0}

    def fake_create(*args, **kwargs):
        nonlocal create_calls
        create_calls["count"] += 1
        if create_calls["count"] == 1:
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"url": "https://api.twenty.com/rest/people", "headers": {"Content-Type": "application/json"}, "payload": {"name": "Marcus Vance", "emails": ["marcus@example.com"], "phones": ["555 888 9999"]}}'
                        )
                    )
                ]
            )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"url": "https://api.twenty.com/rest/people", "headers": {"Content-Type": "application/json"}, "payload": {"name": {"firstName": "Marcus", "lastName": "Vance"}, "emails": {"primaryEmail": "marcus@example.com", "additionalEmails": []}, "phones": {"primaryPhoneNumber": "555 888 9999", "primaryPhoneCountryCode": "", "primaryPhoneCallingCode": "", "additionalPhones": []}}}'
                    )
                )
            ]
        )

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["calls"].append({"url": url, "headers": dict(headers or {}), "payload": json})
        if len(captured["calls"]) == 1:
            class FakeResponse:
                status_code = 400
                text = '{"statusCode":400,"error":"Error","messages":["Provided phone number is invalid (555) 888-9999"],"code":"INVALID_PHONE_NUMBER"}'

                def json(self):
                    return {"error": "Error"}

            return FakeResponse()

        class FakeResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {"id": "twenty-retry-123"}

        return FakeResponse()

    monkeypatch.setattr(dispatch, "get_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatch, "save_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatch.client, "chat", SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(dispatch.requests, "post", fake_post)

    result, _ = dispatch.dispatch_agentic(
        crm_name="twenty",
        user_provided_target="secret-token",
        mapped_data={"first_name": "Marcus", "last_name": "Vance", "email": "marcus@example.com", "phone": "555 888 9999"},
    )

    assert result["id"] == "twenty-retry-123"
    assert captured["calls"][1]["payload"]["name"] == {"firstName": "Marcus", "lastName": "Vance"}
    assert captured["calls"][1]["payload"]["emails"]["primaryEmail"] == "marcus@example.com"
    assert captured["calls"][1]["payload"]["phones"]["primaryPhoneNumber"] == "555 888 9999"


def test_hubspot_dispatch_ignores_example_host_and_forces_official_endpoint(monkeypatch):
    captured = {}

    def fake_create(*args, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"url": "https://api.example.com/resource", "headers": {"Authorization": "Bearer pat-test"}, "payload": {"firstname": "Test"}}'
                    )
                )
            ]
        )

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = json

        class FakeResponse:
            status_code = 200
            text = "ok"

            def json(self):
                return {"id": "123"}

        return FakeResponse()

    monkeypatch.setattr(dispatch, "get_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatch, "save_memory", lambda *args, **kwargs: None)
    monkeypatch.setattr(dispatch.client, "chat", SimpleNamespace(completions=SimpleNamespace(create=fake_create)))
    monkeypatch.setattr(dispatch.requests, "post", fake_post)

    result, trajectory = dispatch.dispatch_agentic(
        crm_name="hubspot",
        user_provided_target="pat-test",
        mapped_data={"firstname": "Test", "lastname": "User"},
    )

    assert captured["url"] == "https://api.hubapi.com/crm/v3/objects/contacts"
    assert captured["headers"]["Authorization"] == "Bearer pat-test"
    assert captured["payload"] == {"properties": {"firstname": "Test", "lastname": "User"}}
    assert result["id"] == "123"
    assert any(step.get("step") == "Dispatch Success" for step in trajectory)
