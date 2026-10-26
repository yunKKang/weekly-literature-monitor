#!/usr/bin/env python3
"""Validate the catalog, optionally checking every ISSN's Crossref identity."""

import argparse
import difflib
import json
import re
import time

from literature_monitor.core.journals import (
    load_journal_config,
    journals_for_pool,
    validate_catalog,
)
from literature_monitor.core.utils import fetch_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    validate_catalog()
    if not args.live:
        print("Catalog structure and ISSN checksums passed")
        return 0
    errors = []
    count = 0

    def normalized(title):
        return re.sub(r"[^a-z0-9]", "", title.lower().replace("&", "and"))

    for pool in load_journal_config()["pools"].values():
        for journal in journals_for_pool(pool):
            count += 1
            status, body = fetch_url(
                "https://api.crossref.org/journals/" + journal["issn"]
            )
            if status != 200:
                errors.append(f"{journal['issn']}: HTTP {status}")
                continue
            actual = json.loads(body)["message"]["title"]
            similarity = difflib.SequenceMatcher(
                None, normalized(journal["title"]), normalized(actual)
            ).ratio()
            print(f"{journal['issn']}: {actual} (identity similarity={similarity:.2f})")
            if similarity < 0.75:
                errors.append(
                    f"{journal['issn']}: configured {journal['title']!r}, actual {actual!r}"
                )
            time.sleep(0.3)
    if errors:
        raise RuntimeError("; ".join(errors))
    print(f"All {count} Crossref journal identities passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
