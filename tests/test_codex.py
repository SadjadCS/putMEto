"""Offline tests for ChatGPT-backed AI, explicit app tools, and chat review gates."""

import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import ai, codex_bridge, codex_tools, db


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "workspace")
    db.initialize()


def run(awaitable):
    return asyncio.run(awaitable)


def test_codex_provider_needs_no_url_model_or_key(monkeypatch):
    calls = []
    class Bridge:
        async def generate_json(self, instructions, data, schema, model=None):
            calls.append((instructions, data, schema, model))
            return '{"ok":true}'
    monkeypatch.setattr(codex_bridge, "get_bridge", lambda: Bridge())
    run(ai.test_connection({"provider": "codex", "model": "", "base_url": "", "api_key": "unused-secret"}))
    assert calls[0][3] is None
    assert calls[0][2] == ai.ConnectionResult.model_json_schema()
    assert "unused-secret" not in json.dumps(calls)


@pytest.mark.parametrize("result", ['{"wrong":true}', 'not JSON', '{"ok":false}'])
def test_codex_validates_structured_output(monkeypatch, result):
    class Bridge:
        async def generate_json(self, *args, **kwargs):
            return result
    monkeypatch.setattr(codex_bridge, "get_bridge", lambda: Bridge())
    with pytest.raises(ai.AIError):
        run(ai.test_connection({"provider": "codex"}))


def test_codex_auth_failure_is_actionable(monkeypatch):
    class Bridge:
        async def generate_json(self, *args, **kwargs):
            raise codex_bridge.CodexError("Sign in with ChatGPT.")
    monkeypatch.setattr(codex_bridge, "get_bridge", lambda: Bridge())
    with pytest.raises(ai.AIError, match="Sign in with ChatGPT"):
        run(ai.test_connection({"provider": "codex"}))


def test_settings_codex_clears_api_key_and_preserves_other_provider_validation():
    from backend.main import app
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        db.mutate_state(lambda state: state["settings"].update(api_key="previous-secret"))
        response = client.put("/api/settings", json={"provider": "codex", "api_key": "ignored-secret"})
        assert response.status_code == 200
        assert response.json() == {"provider": "codex", "model": "", "base_url": "", "api_key_set": False}
        assert db.get_state()["settings"]["api_key"] == ""
        assert client.get("/api/health").json()["ai_configured"]
        assert client.put("/api/settings", json={"provider": "ollama"}).status_code == 422
        assert client.put("/api/settings", json={"provider": "compatible", "base_url": "https://example.com/v1", "model": ""}).status_code == 422


def test_workspace_tool_excludes_secrets_and_chat():
    db.mutate_state(lambda state: state.update(
        settings={"api_key": "must-not-leak"},
        codex_chat={"thread_id": "private-thread", "messages": []},
    ))
    result = run(codex_tools.execute_tool("putmeto_workspace", {}))
    assert "must-not-leak" not in json.dumps(result)
    assert "private-thread" not in json.dumps(result)
    assert "settings" not in result


@pytest.mark.parametrize("name,args", [("shell", {"command": "pwd"}), ("linkedin_fill", {"values": {}}), ("linkedin_search", {"limit": 500}), ("putmeto_workspace", {"url": "https://example.com"})])
def test_tools_reject_unknown_or_invalid_arguments(name, args):
    with pytest.raises(HTTPException):
        run(codex_tools.execute_tool(name, args))


def test_draft_confirmation_requires_exact_unchanged_contents():
    draft = {
        "kind": "project", "title": "Tool", "organization": "", "start": "", "end": "",
        "original": "Built a Python command line tool.", "enhanced": "Developed a command line application using Python.",
    }
    entry = run(codex_tools.execute_tool("putmeto_draft_item", draft))["item"]
    assert entry["confirmed"] is False
    review = {"items": [{key: value for key, value in entry.items() if key != "confirmed"}]}
    assert draft["enhanced"] in codex_tools.approval_description("putmeto_confirm_items", review)
    db.mutate_state(lambda state: state["items"][0].update(enhanced="Changed in another tab."))
    with pytest.raises(HTTPException) as exc:
        run(codex_tools.execute_tool("putmeto_confirm_items", review))
    assert exc.value.status_code == 409
    assert db.get_state()["items"][0]["confirmed"] is False
    review["items"][0]["enhanced"] = "Changed in another tab."
    run(codex_tools.execute_tool("putmeto_confirm_items", review))
    assert db.get_state()["items"][0]["confirmed"] is True


def test_profile_patch_preserves_unmentioned_fields():
    db.mutate_state(lambda state: state["profile"].update(name="Applicant", email="applicant@example.test"))
    run(codex_tools.execute_tool("putmeto_update_profile", {"headline": "Engineer"}))
    profile = db.get_state()["profile"]
    assert profile["name"] == "Applicant"
    assert profile["email"] == "applicant@example.test"
    assert profile["headline"] == "Engineer"


def test_runtime_removes_api_billing_credentials(monkeypatch):
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
        monkeypatch.setenv(name, "sensitive-value")
    environment = codex_bridge.runtime_environment()
    assert not {"OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"}.intersection(environment)
    config = codex_bridge.runtime_config()
    assert config["features.shell_tool"] is False
    assert config["features.code_mode_host"] is True  # Required transport for app tool calls.
    assert config["features.code_mode"] is False
    assert config["web_search"] == "disabled"
    assert config["approval_policy"] == "never"


def test_streamed_messages_persist_and_failed_turn_reports_error():
    async def exercise():
        bridge = codex_bridge.CodexBridge()
        active = codex_bridge.Run("thread", True, asyncio.get_running_loop().create_future())
        bridge._runs["thread"] = active
        bridge._notification("item/agentMessage/delta", {"threadId": "thread", "itemId": "a", "delta": "Hello"})
        bridge._notification("item/agentMessage/delta", {"threadId": "thread", "itemId": "a", "delta": " there"})
        assert bridge.chat_state()["messages"][0]["content"] == "Hello there"
        assert codex_bridge.CodexBridge().chat_state()["messages"][0]["content"] == "Hello there"
        bridge._notification("turn/completed", {"threadId": "thread", "turn": {"status": "failed", "error": {"message": "Quota reached"}}})
        with pytest.raises(codex_bridge.CodexError, match="Quota"):
            await active.done
    run(exercise())


def test_tool_approval_decline_never_executes(monkeypatch):
    async def exercise():
        bridge = codex_bridge.CodexBridge()
        bridge._dynamic_tools()
        bridge._runs["thread"] = codex_bridge.Run("thread", True, asyncio.get_running_loop().create_future())
        calls = []
        async def dispatch(name, args):
            calls.append(name)
            return {"message": "Filled"}
        monkeypatch.setattr(codex_tools, "execute_tool", dispatch)
        task = asyncio.create_task(bridge._call_tool({
            "threadId": "thread", "tool": "linkedin_fill", "callId": "tool-1",
            "arguments": {"snapshot_id": "snapshot", "values": {"email": "applicant@example.test"}},
        }))
        await asyncio.sleep(0)
        pending = bridge.chat_state()["pending"][0]
        assert "applicant@example.test" in pending["description"]
        assert "future" not in pending
        bridge.respond(pending["id"], approved=False)
        result = await task
        assert result["success"] is False
        assert calls == []
        assert not bridge.chat_state()["pending"]
    run(exercise())


def test_no_tools_in_structured_generation_or_unknown_threads(monkeypatch):
    async def exercise():
        bridge = codex_bridge.CodexBridge()
        bridge._dynamic_tools()
        bridge._runs["json"] = codex_bridge.Run("json", False, asyncio.get_running_loop().create_future())
        for thread in ("json", "unknown"):
            response = await bridge._call_tool({"threadId": thread, "tool": "putmeto_workspace", "arguments": {}})
            assert response["success"] is False
    run(exercise())


def test_native_actions_are_denied(monkeypatch):
    async def exercise():
        bridge, replies = codex_bridge.CodexBridge(), []
        async def write(message):
            replies.append(message)
        monkeypatch.setattr(bridge, "_write", write)
        for method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            await bridge._server_request({"id": 1, "method": method, "params": {}})
            assert replies[-1]["result"]["decision"] == "decline"
        await bridge._server_request({"id": 2, "method": "item/permissions/requestApproval", "params": {}})
        assert not replies[-1]["result"]["permissions"]
    run(exercise())


def test_pending_questions_require_all_displayed_answers():
    async def exercise():
        bridge = codex_bridge.CodexBridge()
        task = asyncio.create_task(bridge._ask("thread", kind="question", title="Preference", description="", questions=[{"id": "track", "question": "Industry or academia?"}]))
        await asyncio.sleep(0)
        pending = bridge.chat_state()["pending"][0]
        with pytest.raises(codex_bridge.CodexError):
            bridge.respond(pending["id"], answers={"other": "industry"})
        assert not task.done()
        bridge.respond(pending["id"], answers={"track": "industry"})
        assert (await task)["answers"] == {"track": "industry"}
        with pytest.raises(codex_bridge.CodexError):
            bridge.respond(pending["id"], answers={"track": "industry"})
    run(exercise())


def test_chat_routes_validate_and_preserve_local_request_guards(monkeypatch):
    from backend import codex_routes
    from backend.main import app
    calls = []
    class Bridge:
        async def get_status(self):
            return {"available": True, "authenticated": True}
        def chat_state(self):
            return {"messages": [], "busy": False, "pending": []}
        async def send_message(self, message):
            calls.append(message)
            return {"busy": True}
        def respond(self, request_id, **kwargs):
            assert asyncio.get_running_loop()  # Futures must resolve on the event loop.
            calls.append((request_id, kwargs))
            return {"ok": True}
    monkeypatch.setattr(codex_routes, "get_bridge", lambda: Bridge())
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        assert client.get("/api/codex/status").json()["authenticated"]
        assert client.post("/api/codex/chat/message", json={"message": "hello"}).status_code == 202
        assert calls[-1] == "hello"
        assert client.post("/api/codex/chat/message", json={"message": " "}).status_code == 422
        assert client.post("/api/codex/chat/message", json={"message": "hello", "command": "anything"}).status_code == 422
        assert client.post("/api/codex/chat/message", json={"message": "hi"}, headers={"Origin": "https://example.com"}).status_code == 403
        assert client.post("/api/codex/chat/respond", json={"id": "review", "approved": False}).json()["ok"]
        assert calls[-1] == ("review", {"approved": False, "answers": None})


def test_chat_rejects_concurrent_messages_and_cancels_pending_work(monkeypatch):
    async def exercise():
        bridge = codex_bridge.CodexBridge()
        started, stopped = asyncio.Event(), asyncio.Event()
        async def account():
            pass
        async def chat(message):
            started.set()
            try:
                await asyncio.Future()
            finally:
                stopped.set()
        monkeypatch.setattr(bridge, "_require_account", account)
        monkeypatch.setattr(bridge, "_chat", chat)
        assert (await bridge.send_message("first"))["busy"]
        await started.wait()
        with pytest.raises(codex_bridge.CodexError, match="still working"):
            await bridge.send_message("second")
        with pytest.raises(codex_bridge.CodexError, match="Stop"):
            await bridge.reset()
        await bridge.cancel()
        assert stopped.is_set()
        assert not bridge.busy
        assert len(bridge.chat_state()["messages"]) == 1
        await bridge.reset()
        assert not bridge.chat_state()["messages"]
    run(exercise())


def test_codex_output_schema_requires_defaulted_properties_recursively():
    original = ai.ExtractedResume.model_json_schema()
    before = json.dumps(original, sort_keys=True)
    schema = codex_bridge.strict_output_schema(original)
    def verify(value):
        if isinstance(value, dict):
            if "properties" in value:
                assert value["required"] == list(value["properties"])
                assert value["additionalProperties"] is False
            assert "default" not in value
            for child in value.values():
                verify(child)
        elif isinstance(value, list):
            for child in value:
                verify(child)
    verify(schema)
    assert json.dumps(original, sort_keys=True) == before
    assert "organization" in schema["$defs"]["ExtractedItem"]["required"]
