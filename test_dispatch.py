from types import SimpleNamespace

import dispatch


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
