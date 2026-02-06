"""You.com Search API wrapper for grounding DeepSeek outputs."""

import os
import httpx
from dotenv import load_dotenv

from pathlib import Path
load_dotenv(Path.home() / ".env")
load_dotenv(override=True)

YOU_API_KEY = os.getenv("YOU_API_KEY", "")
YOU_BASE_URL = "https://ydc-index.io/v1/search"


def search(query: str, num_results: int = 5) -> list[dict]:
    """Search You.com and return structured results."""
    resp = httpx.get(
        YOU_BASE_URL,
        params={"query": query, "count": num_results},
        headers={"X-API-Key": YOU_API_KEY},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    results_data = data.get("results", {})
    hits = results_data.get("web", [])
    results = []
    for hit in hits:
        snippets = hit.get("snippets", [])
        results.append({
            "title": hit.get("title", ""),
            "url": hit.get("url", ""),
            "snippet": " ".join(snippets)[:500] if snippets else hit.get("description", "")[:500],
        })
    return results


def search_and_format(query: str, num_results: int = 5) -> str:
    """Search and return a formatted string for LLM consumption."""
    results = search(query, num_results)
    if not results:
        return "No results found."
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"[{i}] {r['title']}\n    URL: {r['url']}\n    {r['snippet']}")
    return "\n\n".join(parts)
