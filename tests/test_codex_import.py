"""Structured resume processing does not inherit long-running coding effort."""

import asyncio

import pytest

from backend import db
from backend.codex_bridge import CodexBridge, CodexError


@pytest.fixture(autouse=True)
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "workspace")
    db.initialize()


def test_resume_generation_uses_low_effort_without_changing_chat(monkeypatch):
    async def exercise():
        bridge, calls = CodexBridge(), []
        bridge._effective_config["model_reasoning_effort"] = "ultra"

        async def account():
            pass

        async def rpc(method, params, **kwargs):
            calls.append((method, params))
            if method == "thread/start":
                return {"thread": {"id": "extraction"}}
            if method == "turn/start":
                thread_id = params["threadId"]
                bridge._notification("item/completed", {
                    "threadId": thread_id, "item": {"type": "agentMessage", "id": thread_id + "-answer", "text": '{"ok":true}'},
                })
                bridge._notification("turn/completed", {"threadId": thread_id, "turn": {"status": "completed"}})
                return {"turn": {"id": "turn"}}
            return {}

        monkeypatch.setattr(bridge, "_require_account", account)
        monkeypatch.setattr(bridge, "_rpc", rpc)
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        assert await bridge.generate_json("Extract synthetic data.", {"cv_text": "Synthetic resume."}, schema, model="gpt-6-astra") == '{"ok":true}'
        extraction_turn = next(params for method, params in calls if method == "turn/start")
        assert extraction_turn["effort"] == "low"
        assert extraction_turn["model"] == "gpt-6-astra"
        assert bridge._effective_config["model_reasoning_effort"] == "ultra"
        assert "required" not in schema
        await bridge._turn("chat", "Hello", visible=True)
        chat_turn = [params for method, params in calls if method == "turn/start"][-1]
        assert "effort" not in chat_turn
        assert calls[-2][0] == "thread/unsubscribe"

    asyncio.run(exercise())


def test_timed_out_generation_interrupts_and_releases_thread(monkeypatch):
    async def exercise():
        bridge, calls = CodexBridge(), []

        async def account():
            pass

        async def rpc(method, params, **kwargs):
            calls.append((method, params))
            if method == "thread/start":
                return {"thread": {"id": "extraction"}}
            if method == "turn/start":
                return {"turn": {"id": "unfinished-turn"}}
            return {}

        async def immediate_timeout(awaitable, timeout):
            assert timeout == 240
            awaitable.cancel()
            raise asyncio.TimeoutError

        monkeypatch.setattr(bridge, "_require_account", account)
        monkeypatch.setattr(bridge, "_rpc", rpc)
        monkeypatch.setattr(asyncio, "wait_for", immediate_timeout)
        with pytest.raises(CodexError, match="took too long"):
            await bridge.generate_json("Extract synthetic data.", {}, {"type": "object", "properties": {}})
        assert ("turn/interrupt", {"threadId": "extraction", "turnId": "unfinished-turn"}) in calls
        assert calls[-1][0] == "thread/unsubscribe"
        assert not bridge._runs
        assert not db.get_state()["items"]

    asyncio.run(exercise())


def test_cancelled_generation_interrupts_and_releases_thread(monkeypatch):
    async def exercise():
        bridge, calls = CodexBridge(), []
        started = asyncio.Event()

        async def account():
            pass

        async def rpc(method, params, **kwargs):
            calls.append((method, params))
            if method == "thread/start":
                return {"thread": {"id": "extraction"}}
            if method == "turn/start":
                started.set()
                return {"turn": {"id": "unfinished-turn"}}
            return {}

        monkeypatch.setattr(bridge, "_require_account", account)
        monkeypatch.setattr(bridge, "_rpc", rpc)
        task = asyncio.create_task(bridge.generate_json("Extract synthetic data.", {}, {"type": "object", "properties": {}}))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ("turn/interrupt", {"threadId": "extraction", "turnId": "unfinished-turn"}) in calls
        assert calls[-1][0] == "thread/unsubscribe"
        assert not bridge._runs
        assert not db.get_state()["items"]

    asyncio.run(exercise())


def test_pdf_pages_reach_codex_as_full_resolution_images(monkeypatch):
    async def exercise():
        bridge, calls, timeouts = CodexBridge(), [], []
        original_wait_for = asyncio.wait_for

        async def account():
            pass

        async def rpc(method, params, **kwargs):
            calls.append((method, params))
            if method == "thread/start":
                return {"thread": {"id": "reading"}}
            if method == "turn/start":
                bridge._notification("item/completed", {
                    "threadId": "reading", "item": {"type": "agentMessage", "id": "answer", "text": '{"lines":[]}'},
                })
                bridge._notification("turn/completed", {"threadId": "reading", "turn": {"status": "completed"}})
                return {"turn": {"id": "turn"}}
            return {}

        async def record_timeout(awaitable, timeout):
            timeouts.append(timeout)
            return await original_wait_for(awaitable, timeout)

        monkeypatch.setattr(bridge, "_require_account", account)
        monkeypatch.setattr(bridge, "_rpc", rpc)
        monkeypatch.setattr(asyncio, "wait_for", record_timeout)
        await bridge.generate_json("Transcribe.", {"page_count": 2}, {"type": "object", "properties": {}}, images=[b"one", b"two"])
        text, *pictures = next(params for method, params in calls if method == "turn/start")["input"]
        assert text["type"] == "text" and '"page_count": 2' in text["text"]
        assert pictures == [{"type": "image", "url": "data:image/png;base64,b25l", "detail": "original"},
                            {"type": "image", "url": "data:image/png;base64,dHdv", "detail": "original"}]
        assert timeouts == [360]

    asyncio.run(exercise())
