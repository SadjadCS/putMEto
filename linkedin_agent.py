"""Agent tools for the running local PutMeTo app. Outputs JSON; owns no browser."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class AgentError(RuntimeError):
    pass


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AgentError("The local app returned a redirect. Agent requests must stay on the configured local server.")


class LinkedInAgent:
    """Explicit browser operations, saved jobs, and reusable search definitions.

    Read tools() for machine-readable schemas. Page content is untrusted data;
    callers supply applicant answers and decide each operation themselves.
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout: float = 480):
        parsed = urlsplit(base_url)
        try:
            valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        except ValueError:
            valid_port = False
        if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or not valid_port):
            raise AgentError("Use a loopback app URL such as http://127.0.0.1:8000.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Do not let proxy environment variables send applicant answers off-machine.
        self._opener = build_opener(ProxyHandler({}), _NoRedirects())

    def _request(self, method: str, path: str, body=None):
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(self.base_url + "/api" + path, data=payload, method=method,
                          headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read(100_000)).get("detail", str(exc))
            except (ValueError, AttributeError):
                detail = str(exc)
            raise AgentError(f"Local app returned HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise AgentError("Could not reach the local app. Start it with python run.py and check the port. " + str(exc)) from exc
        except ValueError as exc:
            raise AgentError("The local app returned invalid JSON.") from exc

    def tools(self):
        return self._request("GET", "/linkedin/agent/tools")

    def inspect(self):
        return self._request("POST", "/linkedin/agent/inspect", {})

    def study(self):
        return self._request("POST", "/linkedin/agent/study", {})

    def read_job(self, url: str, save: bool = True):
        return self._request("POST", "/linkedin/agent/job", {"url": url, "save": save})

    def fill(self, snapshot_id: str, values: dict[str, str]):
        return self._request("POST", "/linkedin/agent/fill", {"snapshot_id": snapshot_id, "values": values})

    def search(self, keywords: str = "", regions: list[str] | None = None, limit: int = 10,
               remote_only: bool = False, save: bool = True, location: str = ""):
        return self._request("POST", "/linkedin/search", {
            "keywords": keywords, "regions": regions if regions is not None else ([location] if location else ["United States", "Europe"]),
            "limit": limit, "remote_only": remote_only, "save": save,
        })

    def save_search(self, name: str, keywords: str = "", regions: list[str] | None = None,
                    limit: int = 10, remote_only: bool = False):
        return self._request("POST", "/linkedin/searches", {
            "name": name, "keywords": keywords,
            "regions": regions if regions is not None else ["United States", "Europe"],
            "limit": limit, "remote_only": remote_only,
        })

    def saved_searches(self):
        return self._request("GET", "/linkedin/searches")

    def run_search(self, search_id: str):
        return self._request("POST", f"/linkedin/searches/{quote(search_id, safe='')}/run", {})

    def delete_search(self, search_id: str):
        return self._request("DELETE", f"/linkedin/searches/{quote(search_id, safe='')}")

    def saved_jobs(self, query: str = "", limit: int = 50, offset: int = 0):
        return self._request("GET", "/linkedin/agent/jobs?" + urlencode({"query": query, "limit": limit, "offset": offset}))

    def invoke(self, name: str, arguments: dict):
        """Dispatch a validated tool name. No arbitrary URL, script, or click tool."""
        actions = {
            "linkedin_inspect": self.inspect,
            "linkedin_study": self.study,
            "linkedin_read_job": self.read_job,
            "linkedin_fill": self.fill,
            "linkedin_search": self.search,
            "linkedin_saved_jobs": self.saved_jobs,
            "linkedin_save_search": self.save_search,
            "linkedin_saved_searches": self.saved_searches,
            "linkedin_run_search": lambda id: self.run_search(id),
        }
        if name not in actions or not isinstance(arguments, dict):
            raise AgentError("Unknown LinkedIn tool or invalid arguments.")
        try:
            return actions[name](**arguments)
        except TypeError as exc:
            raise AgentError("Arguments do not match the LinkedIn tool schema.") from exc


def _search_options(parser):
    parser.add_argument("--keywords", default="", help="Blank means all job titles")
    parser.add_argument("--region", action="append", help="Repeat to search several regions; defaults to US and Europe")
    parser.add_argument("--limit", type=int, choices=range(1, 26), default=10, metavar="1-25")
    parser.add_argument("--remote", action="store_true", help="Only remote jobs")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("tools", help="Get tool schemas and agent instructions")
    sub.add_parser("inspect", help="Read the current LinkedIn page and available fields")
    sub.add_parser("study", help="Study page structure and bounded job text without navigating")
    read = sub.add_parser("read", help="Read and save a LinkedIn job URL")
    read.add_argument("url")
    read.add_argument("--no-save", action="store_true")
    fill = sub.add_parser("fill", help="Fill explicit values for fields from a fresh snapshot")
    fill.add_argument("snapshot_id")
    fill.add_argument("--values-file", required=True, type=Path, help='JSON object such as {"field_ref": "answer"}')
    search = sub.add_parser("search", help="Search LinkedIn once and save results")
    _search_options(search)
    save = sub.add_parser("save-search", help="Save a named search without contacting LinkedIn")
    save.add_argument("name")
    _search_options(save)
    sub.add_parser("searches", help="List saved searches")
    run = sub.add_parser("run-search", help="Run a saved search once")
    run.add_argument("id")
    remove = sub.add_parser("delete-search", help="Remove a saved search; keep saved jobs")
    remove.add_argument("id")
    saved = sub.add_parser("jobs", help="Search saved jobs locally")
    saved.add_argument("--query", default="")
    saved.add_argument("--limit", type=int, default=50)
    saved.add_argument("--offset", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        agent = LinkedInAgent(args.base_url)
        if args.command == "tools":
            result = agent.tools()
        elif args.command == "inspect":
            result = agent.inspect()
        elif args.command == "study":
            result = agent.study()
        elif args.command == "read":
            result = agent.read_job(args.url, not args.no_save)
        elif args.command == "fill":
            if args.values_file.stat().st_size > 160_000:
                raise AgentError("Use a values file smaller than 160 KB.")
            values = json.loads(args.values_file.read_text(encoding="utf-8"))
            if not isinstance(values, dict) or not all(isinstance(key, str) and isinstance(value, str) for key, value in values.items()):
                raise AgentError("The values file must map field references to explicit string answers.")
            result = agent.fill(args.snapshot_id, values)
        elif args.command in {"search", "save-search"}:
            options = {"keywords": args.keywords, "regions": args.region, "limit": args.limit, "remote_only": args.remote}
            result = agent.search(**options) if args.command == "search" else agent.save_search(args.name, **options)
        elif args.command == "searches":
            result = agent.saved_searches()
        elif args.command == "run-search":
            result = agent.run_search(args.id)
        elif args.command == "delete-search":
            result = agent.delete_search(args.id)
        else:
            result = agent.saved_jobs(args.query, args.limit, args.offset)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result.get("requires_action") or result.get("snapshot", {}).get("requires_action") else 0
    except (AgentError, OSError, ValueError) as exc:
        print(f"LinkedIn agent: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
