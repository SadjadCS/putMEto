"""Search LinkedIn Jobs in a visible Camoufox browser.

Example: python linkedin_search.py --limit 10 --output jobs.json
Defaults to all job titles across the United States and Europe.
"""

import argparse
import asyncio
import json
from pathlib import Path

from backend.linkedin import close_linkedin, linkedin_status
from backend.jobs import search_linkedin_regions
from backend.network import SourceError


async def run(args):
    try:
        regions = args.region or ([args.location] if args.location else ["United States", "Europe"])
        while True:
            result = await search_linkedin_regions(args.keywords, regions, args.remote, args.limit)
            if result["requires_action"] and args.wait_for_login:
                print(result["message"], flush=True)
                await asyncio.to_thread(input, "Complete any login or verification in Camoufox, then press Enter to retry (Ctrl+C to stop): ")
                continue
            break
        serialized = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(serialized + "\n", encoding="utf-8")
            print(f"Found {len(result['jobs'])} jobs. Results saved to {args.output}.")
            print(result["message"], flush=True)
        else:
            print(serialized, flush=True)
        if args.keep_open:
            print("Camoufox is open. Close its window or press Ctrl+C to finish.", flush=True)
            while linkedin_status().get("active"):
                await asyncio.sleep(1)
        return 2 if result["requires_action"] else 0
    finally:
        await close_linkedin()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("keywords", nargs="?", default="", help="Optional keywords; omit to search all job titles")
    parser.add_argument("--location", default="", help="One city, region, or country, replacing the default US and Europe search")
    parser.add_argument("--region", action="append", help="Region to search; repeat up to five times, e.g. --region Germany --region France")
    parser.add_argument("--remote", action="store_true", help="Request remote jobs")
    parser.add_argument("--limit", type=int, choices=range(1, 26), default=10, metavar="1-25", help="Maximum jobs per region (default:10)")
    parser.add_argument("--output", type=Path, help="Write the full result to a JSON file")
    parser.add_argument("--keep-open", action="store_true", help="Leave the visible browser open after searching")
    parser.add_argument("--wait-for-login", action="store_true", help="Allow manual login or verification and retry when you press Enter")
    args = parser.parse_args()
    if args.region and (len(args.region) > 5 or any(not value.strip() or len(value) > 300 for value in args.region)):
        parser.error("Choose up to five nonempty regions, each 300 characters or fewer.")
    try:
        return asyncio.run(run(args))
    except (KeyboardInterrupt, EOFError):
        return 130
    except SourceError as error:
        parser.exit(1, f"LinkedIn search: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
