# Codex in PutMeTo

PutMeTo starts the official runtime bundled by `openai-codex==0.157.1` as an App Server subprocess. It communicates over local standard input/output JSON-RPC. The pinned runtime avoids relying on a global Codex executable. The SDK currently omits the experimental dynamic-tool fields from its typed client, so the bridge uses the runtime protocol directly.

`backend/codex_bridge.py` handles initialization, managed ChatGPT sign-in, model discovery, conversation resumption, incremental messages, structured JSON turns, cancellation and pending questions/approvals. `backend/codex_routes.py` exposes only the following application endpoints, protected by the app's localhost and same-origin guard:

| Endpoint | Purpose |
| --- | --- |
| `GET /api/codex/status` | Runtime, managed account and model availability; never tokens |
| `POST /api/codex/login` | Start managed ChatGPT browser sign-in |
| `GET /api/codex/chat` | Saved messages, busy/error status and pending user requests |
| `POST /api/codex/chat/message` | Start a turn with `{message}`; returns 202 |
| `POST /api/codex/chat/cancel` | Interrupt the current chat turn |
| `POST /api/codex/chat/reset` | Start a new conversation while retaining workspace data |
| `POST /api/codex/chat/respond` | Answer `{id, approved}` or `{id, answers:{question_id:text}}` |

The frontend polls while a response is active and updates messages without replacing the composer. Navigation away stops polling and refreshes workspace data. HTML in responses is rendered as text. Login links must be HTTPS on an official OpenAI/ChatGPT authentication host.

`backend/codex_tools.py` supplies a fixed dynamic-tool catalog and validates all arguments using Pydantic before calling existing application services. Resume drafts cannot mark themselves confirmed. Confirmation tools require explicit approval of exact contents and reject entries changed since the proposal. LinkedIn form filling requires approval of the exact answer mapping and then uses the existing fresh-snapshot checks. There is no final-submit or external-message tool. Unknown tools are rejected.

Native shell execution, code mode, browsing, plugins, hooks and related features are disabled; inherited MCP servers are disabled for these threads. The separate `code_mode_host` transport remains enabled because the pinned runtime requires it to dispatch application tools. Tools explicitly use `deferLoading:false`. Threads use a read-only sandbox and native execution/file/permission approval requests are denied. The worker's working directory is `data/codex-worker`, not the project source directory. The runtime receives the user's existing Codex environment with API-key and alternate OpenAI endpoint variables removed. Authentication is queried through `account/read`; PutMeTo never parses Codex's auth files.

Existing resume AI operations use `generate_json()` in separate ephemeral threads, with no application tools and an output schema. These mechanical extraction and formatting requests explicitly use low reasoning effort so they do not inherit a coding session's slower reasoning setting. This permits a chat tool to call an AI operation without waiting for its own chat turn to finish. A copied schema is normalized to Codex's strict format, including required defaulted fields inside nested definitions. Unknown text remains empty. Output still passes the existing Pydantic checks before the app saves it. A blank Codex model selects the account's default; compatible API and Ollama configuration retain their existing validation.

The `codex_chat` SQLite state entry stores the runtime thread ID and latest 200 display messages. The runtime manages its own conversation persistence. `/api/state` excludes this chat entry and all saved API keys. Cancelling a turn removes pending approvals/questions; approvals are never automatically replayed after a restart. Already completed local changes remain saved.

Tests: `python -m pytest tests/test_codex.py -q` runs without a ChatGPT account or network calls. Live inference additionally needs a supported ChatGPT account, internet access, and a host environment where Codex can use its normal account/state storage.

`python tests/browser_codex_smoke.py` exercises the UI against mocked API responses using Playwright and installed Chrome on macOS. It covers sign-in, streaming updates, questions/approvals, cancellation, provider switching, draft preservation, safe text rendering, and mobile layout. Live checks also verified managed ChatGPT authentication, structured connection responses, CV extraction and chat tool dispatch with synthetic data, conversation resumption in a new runtime, and the running application's Assistant/Settings pages.
