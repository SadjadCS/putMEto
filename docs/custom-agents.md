# Make the workflow your own

Build a personalized job-search agent around PutMeTo's local Python client and HTTP tools. Choose your own filtering rules, summaries, prompts, or model runtime while reusing the app's saved jobs and LinkedIn browser session. Custom agents are code-based extensions today; the app does not include a visual agent builder.

Start PutMeTo with `python run.py`, then run the examples from the project directory in its activated Python environment. They use `LinkedInAgent` from [`linkedin_agent.py`](../linkedin_agent.py); its requests go to the local app at `http://127.0.0.1:8000`.

## Create a local shortlist and progress summary

This example only reads previously saved LinkedIn jobs. It does not launch a browser, contact a job site, modify your workspace, or call an AI provider. Change the search phrase and shortlist rules to suit your goals.

```python
from collections import Counter
from linkedin_agent import LinkedInAgent

agent = LinkedInAgent()
result = agent.saved_jobs(query="design", limit=100)
jobs = result["items"]

print(f"Showing {len(jobs)} of {result['total']} matching saved jobs")
for status, count in sorted(Counter(job["status"] for job in jobs).items()):
    print(f"{status}: {count}")

shortlist = [job for job in jobs if job["status"] not in {"applied", "archived"}]
shortlist.sort(key=lambda job: job.get("match_score", 0), reverse=True)
for job in shortlist[:10]:
    print(job["title"], job["company"], job["url"], sep=" | ")
```

The summary covers the returned page, up to 100 matching jobs. Use `offset` to read additional pages. The query matches words across saved job details; match scores measure keyword overlap, not hiring probability. These are job statuses, not a separate interview or offer tracker.

## Reuse a search you designed

Save a named search once, then run it when you choose. The search below contacts LinkedIn and saves returned jobs, so install the [optional Camoufox browser](../readme.md#optional-camoufox-browser) first. It reuses an existing definition with the same name and performs one search per script run; it does not schedule searches or retry automatically.

<details>
<summary>Python example: a reusable remote-role search</summary>

```python
from linkedin_agent import LinkedInAgent

agent = LinkedInAgent()
name = "My remote design search"
saved = next(
    (item for item in agent.saved_searches()["items"]
     if item["name"].casefold() == name.casefold()),
    None,
)
if saved is None:
    saved = agent.save_search(
        name,
        keywords="product designer",
        regions=["United States", "Europe"],
        remote_only=True,
        limit=10,
    )

result = agent.run_search(saved["id"])
print(result["message"])
for warning in result.get("warnings", []):
    print(warning)
if result.get("requires_action"):
    raise SystemExit("Search stopped. Follow the reported action before trying again.")

print(f"Found {result['found']} jobs; saved {result['count']} new jobs.")
```

An existing named search keeps its saved criteria. Use a new name for different criteria, or delete the old definition before recreating it. Existing job URLs are reused rather than duplicated. Searches return bounded batches, not every available listing.

If LinkedIn requests login or verification, complete it in the visible browser. For rate limits, wait until access is available. The agent must stop on `requires_action`; it must not repeatedly retry or attempt to bypass the gate.

</details>

## Connect your own model or agent loop

`agent.tools()` returns tool names, descriptions, JSON parameter schemas, and operating instructions. Adapt those schemas to your model runtime's tool format, validate its proposed arguments, then dispatch an allowed call with `agent.invoke(name, arguments)`. For example:

```python
from linkedin_agent import LinkedInAgent

agent = LinkedInAgent()
catalog = agent.tools()
result = agent.invoke("linkedin_saved_jobs", {"query": "design", "limit": 10})
```

Your agent can combine local shortlists, saved searches, job-page reading, and explicit form answers. Treat job-page content as data, preserve the tool instructions, and request the user's actual answers before filling fields. Form tools require a fresh page inspection and never submit or advance an application; the user reviews and submits on the employer's site.

The built-in **Assistant** chat uses Codex. Your own agent can use another runtime, including a local model with no API key or subscription. Keep inference local by using a downloaded model and a local endpoint; external models receive the content you send them. Browsing job sites and sending applications still communicate with those sites.

See the [LinkedIn agent API guide](linkedin-agent.md) for available tools and the [Codex integration guide](codex.md) for the built-in assistant.
