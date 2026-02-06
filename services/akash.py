"""DeepSeek V3.2 client via Akash ML (OpenAI-compatible)."""

import os
import json
import httpx
from dotenv import load_dotenv

load_dotenv()

AKASH_API_KEY = os.getenv("AKASH_API_KEY", "")
AKASH_BASE_URL = os.getenv("AKASH_BASE_URL", "https://chatapi.akash.network/api/v1")
AKASH_MODEL = os.getenv("AKASH_MODEL", "DeepSeek-V3-0324")


def chat(messages: list[dict], temperature: float = 0.7, max_tokens: int = 4096) -> str:
    """Send a chat completion request to DeepSeek V3.2 via Akash ML."""
    resp = httpx.post(
        f"{AKASH_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {AKASH_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": AKASH_MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def classify_conversations(conversations_batch: list[dict]) -> list[dict]:
    """Given a batch of conversation summaries, return rabbit hole classifications.

    Each conversation summary has: title, first_messages (str), message_count, created_at.
    Returns list of {name, description, conversation_ids}.
    """
    summaries = "\n".join(
        f"- ID: {c['id']} | Title: \"{c['title']}\" | Messages: {c['message_count']} | Sample: {c['first_messages'][:300]}"
        for c in conversations_batch
    )

    prompt = f"""You are analyzing a user's ChatGPT conversation history to identify "rabbit holes" -- recurring topics or deep dives the user keeps exploring.

Below are conversation summaries. Group them into thematic rabbit holes. A rabbit hole is a topic the user has explored across one or more conversations.

Conversations:
{summaries}

Return a JSON array of rabbit holes. Each rabbit hole:
- "name": short topic name (3-6 words)
- "description": 1-2 sentence description of this rabbit hole
- "conversation_ids": array of conversation IDs that belong to this rabbit hole

Rules:
- A conversation can belong to multiple rabbit holes
- Ignore trivial/one-off conversations (translations, simple lookups)
- Focus on substantive intellectual explorations
- Return ONLY valid JSON, no markdown fences"""

    raw = chat([{"role": "user", "content": prompt}], temperature=0.3, max_tokens=4096)
    # Strip potential markdown fences
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()
    return json.loads(cleaned)


def generate_research_queries(rabbit_hole_name: str, description: str, recent_insights: str) -> list[str]:
    """Generate web search queries for a rabbit hole."""
    prompt = f"""You are a research assistant. Given this "rabbit hole" topic the user has been exploring, generate 2-3 specific web search queries to find new developments, insights, or resources.

Rabbit Hole: {rabbit_hole_name}
Description: {description}
Recent insights (if any): {recent_insights or "None yet"}

Return ONLY a JSON array of search query strings. No markdown, no explanation."""

    raw = chat([{"role": "user", "content": prompt}], temperature=0.5, max_tokens=512)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    return json.loads(cleaned.strip())


def synthesize_research(rabbit_hole_name: str, description: str, search_results: str) -> dict:
    """Synthesize search results into an insight."""
    prompt = f"""You are analyzing web search results for a user's rabbit hole topic.

Rabbit Hole: {rabbit_hole_name}
Description: {description}

Search Results:
{search_results}

Analyze these results and return a JSON object:
- "insight": A concise paragraph summarizing new findings or developments relevant to this rabbit hole
- "should_revisit": true/false -- should the user actively revisit this topic?
- "urgency": "high", "medium", or "low"
- "reason": Why this urgency level

Return ONLY valid JSON, no markdown fences."""

    raw = chat([{"role": "user", "content": prompt}], temperature=0.4, max_tokens=1024)
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    return json.loads(cleaned.strip())


def generate_daily_plan(rabbit_holes_with_insights: list[dict]) -> str:
    """Generate a daily action plan from rabbit holes and their latest insights."""
    context = "\n".join(
        f"- [{rh['priority_score']:.1f}] {rh['name']}: {rh.get('latest_insight', 'No recent insights')} (urgency: {rh.get('urgency', 'low')})"
        for rh in rabbit_holes_with_insights
    )

    prompt = f"""You are a personal knowledge coach. Based on the user's rabbit holes and latest research insights, create a concise daily action plan.

Rabbit Holes (sorted by priority):
{context}

Create a structured daily plan with:
1. TOP PRIORITY: The 1-2 rabbit holes to actively work on today, with specific actions
2. QUICK WINS: 1-2 things that can be done in 15 minutes
3. WATCH LIST: Topics with new developments to keep an eye on
4. PARKED: Topics that can wait

Be specific and actionable. Keep it concise."""

    return chat([{"role": "user", "content": prompt}], temperature=0.5, max_tokens=1500)
