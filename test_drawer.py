#!/usr/bin/env python3
"""Test program that sends a GET request to /open_drawer on the Epson TM-m30 printer server."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send a GET /open_drawer request to the Epson TM-m30 printer server."
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Printer server host or IP address (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8081,
        help="Printer server TCP port (default: 8081)",
    )
    parser.add_argument(
        "--pin",
        type=int,
        choices=[2, 5],
        default=2,
        help="Cash drawer kick pin (2 or 5, default: 2)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Ask the server to simulate opening the cash drawer without USB hardware",
    )
    args = parser.parse_args(argv)

    params: dict[str, str] = {"pin": str(args.pin)}
    if args.mock:
        params["mock"] = "1"

    query_str = urllib.parse.urlencode(params)
    url = f"http://{args.host}:{args.port}/open_drawer?{query_str}"

    print(f"Sending GET request to {url} ...")
    req = urllib.request.Request(url, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            print(f"Server responded with HTTP {resp.status}:")
            print(json.dumps(resp_data, indent=2, ensure_ascii=False))
            return 0
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        print(f"HTTP error {exc.code}: {err_body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Failed to connect to printer server at {url}: {exc.reason}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

