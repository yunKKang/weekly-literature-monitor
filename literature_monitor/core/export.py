"""Export search results to common research formats."""

from __future__ import annotations

import csv
import io
import re
from typing import Any


def export_results(results: list[dict[str, Any]], fmt: str) -> tuple[str, str]:
    fmt = fmt.lower()
    if fmt == "csv":
        return "text/csv", export_csv(results)
    if fmt == "bibtex":
        return "application/x-bibtex", export_bibtex(results)
    if fmt == "markdown":
        return "text/markdown", export_markdown(results)
    raise ValueError(f"Unsupported export format: {fmt}")


def export_csv(results: list[dict[str, Any]]) -> str:
    output = io.StringIO()
    fields = [
        "title",
        "journal",
        "year",
        "publication_date",
        "doi",
        "url",
        "total_score",
        "relevance_level",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for item in results:
        writer.writerow({field: item.get(field, "") for field in fields})
    return output.getvalue()


def export_bibtex(results: list[dict[str, Any]]) -> str:
    entries = []
    for index, item in enumerate(results, 1):
        key = bib_key(item, index)
        authors = " and ".join(item.get("authors", []))
        fields = {
            "title": item.get("title"),
            "author": authors,
            "year": item.get("year"),
            "journal": item.get("journal"),
            "doi": item.get("doi"),
            "url": item.get("url"),
        }
        body = "\n".join(
            f"  {name} = {{{escape_bibtex(value)}}},"
            for name, value in fields.items()
            if value
        )
        entries.append(f"@article{{{key},\n{body}\n}}")
    return "\n\n".join(entries) + ("\n" if entries else "")


def export_markdown(results: list[dict[str, Any]]) -> str:
    lines = ["# Literature Search Results", ""]
    for index, item in enumerate(results, 1):
        doi = item.get("doi") or "N/A"
        url = item.get("url") or (f"https://doi.org/{doi}" if doi != "N/A" else "")
        title = item.get("title") or "Untitled"
        lines.extend(
            [
                f"## {index}. {title}",
                "",
                f"- Journal: {item.get('journal') or 'N/A'}",
                f"- Published: {item.get('publication_date') or item.get('year') or 'N/A'}",  # noqa: E501
                f"- Relevance: {item.get('relevance_level')} ({item.get('total_score')})",  # noqa: E501
                f"- DOI: {doi}",
            ]
        )
        if url:
            lines.append(f"- URL: {url}")
        if item.get("abstract"):
            lines.extend(["", item["abstract"][:1200], ""])
        lines.append("")
    return "\n".join(lines)


def bib_key(item: dict[str, Any], index: int) -> str:
    author = "paper"
    authors = item.get("authors") or []
    if authors:
        author = re.sub(r"[^A-Za-z0-9]+", "", authors[0].split(",")[0]) or "paper"
    year = item.get("year") or "nd"
    return f"{author}{year}_{index}"


def escape_bibtex(value: Any) -> str:
    return str(value).replace("{", "\\{").replace("}", "\\}")

