"""Every test runs against a throwaway data folder, never the real workspace."""

import pytest

from backend import auto_apply, codex_bridge, db, goldmove, job_matching, linkedin, skill_grouping, term_bags


class NoCodex:
    """Tests never start the real Codex; a test that needs it supplies its own bridge."""

    async def close(self):
        pass  # The app closes the bridge when it shuts down.

    def __getattr__(self, name):
        async def unavailable(*args, **kwargs):
            raise codex_bridge.CodexError("Codex is not available in tests.")
        return unavailable


@pytest.fixture(autouse=True)
def isolated_data(tmp_path_factory, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path_factory.mktemp("data"))
    monkeypatch.setattr(linkedin, "navigations", linkedin.deque(maxlen=2000))
    monkeypatch.setattr(linkedin, "_navigations_loaded", False)
    monkeypatch.setattr(codex_bridge, "get_bridge", lambda: NoCodex())
    # Matching runs AI per job; tests that cover it start it themselves.
    monkeypatch.setattr(job_matching, "kick", lambda: None)
    # Auto-apply drives a real browser; tests that cover it run it themselves.
    monkeypatch.setattr(auto_apply, "kick", lambda: None)
    monkeypatch.setattr(skill_grouping, "kick", lambda force=False: None)
    monkeypatch.setattr(term_bags, "kick", lambda force=False: None)
    monkeypatch.setattr(goldmove, "kick", lambda: None)
