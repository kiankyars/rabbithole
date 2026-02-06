"""Autonomous research agent -- runs on a schedule, discovers insights, generates daily plans."""

import json
from datetime import datetime, timezone, date

from db import execute, execute_one, get_conn
from services.akash import generate_research_queries, synthesize_research, generate_daily_plan
from services.yousearch import search_and_format


def get_stale_rabbit_holes(limit: int = 5) -> list[dict]:
    """Get rabbit holes most in need of research, sorted by priority and staleness."""
    return execute(
        """SELECT id, name, description, priority_score, last_researched_at, status
           FROM rabbit_holes
           WHERE status = 'active'
           ORDER BY last_researched_at ASC NULLS FIRST, priority_score DESC
           LIMIT %s""",
        (limit,),
        fetch=True,
    )


def get_recent_insights(rabbit_hole_id: int, limit: int = 3) -> str:
    """Get recent insights for a rabbit hole as a formatted string."""
    rows = execute(
        """SELECT content, created_at FROM insights
           WHERE rabbit_hole_id = %s
           ORDER BY created_at DESC LIMIT %s""",
        (rabbit_hole_id, limit),
        fetch=True,
    )
    if not rows:
        return ""
    return "\n".join(f"- [{r['created_at']}] {r['content'][:200]}" for r in rows)


def research_rabbit_hole(rh: dict) -> dict:
    """Run a full research cycle on a single rabbit hole."""
    rh_id = rh["id"]
    name = rh["name"]
    desc = rh["description"] or ""

    recent = get_recent_insights(rh_id)

    # Step 1: Generate search queries via DeepSeek
    queries = generate_research_queries(name, desc, recent)
    print(f"  Generated queries: {queries}")

    # Step 2: Search You.com for each query
    all_results = []
    for q in queries:
        formatted = search_and_format(q, num_results=3)
        all_results.append(f"Query: {q}\n{formatted}")

    combined_results = "\n\n---\n\n".join(all_results)

    # Step 3: Synthesize with DeepSeek
    synthesis = synthesize_research(name, desc, combined_results)
    print(f"  Synthesis: urgency={synthesis.get('urgency')}, revisit={synthesis.get('should_revisit')}")

    # Step 4: Store results
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()

    # Store insight
    cur.execute(
        """INSERT INTO insights (rabbit_hole_id, content, grounded, urgency)
           VALUES (%s, %s, %s, %s)""",
        (rh_id, synthesis.get("insight", ""), True, synthesis.get("urgency", "low")),
    )

    # Store research run
    cur.execute(
        """INSERT INTO research_runs (rabbit_hole_id, query_sent, deepseek_response, you_com_results)
           VALUES (%s, %s, %s, %s)""",
        (rh_id, json.dumps(queries), json.dumps(synthesis), combined_results[:10000]),
    )

    # Update rabbit hole
    cur.execute(
        """UPDATE rabbit_holes SET last_researched_at = NOW(), updated_at = NOW() WHERE id = %s""",
        (rh_id,),
    )

    cur.close()
    conn.close()

    return synthesis


def build_daily_plan():
    """Generate and store a daily action plan."""
    # Get all active rabbit holes with their latest insights
    holes = execute(
        """SELECT rh.id, rh.name, rh.description, rh.priority_score, rh.last_researched_at,
                  i.content AS latest_insight, i.urgency
           FROM rabbit_holes rh
           LEFT JOIN LATERAL (
               SELECT content, urgency FROM insights
               WHERE rabbit_hole_id = rh.id
               ORDER BY created_at DESC LIMIT 1
           ) i ON true
           WHERE rh.status = 'active'
           ORDER BY rh.priority_score DESC""",
        fetch=True,
    )

    plan_text = generate_daily_plan(holes)

    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO daily_plans (plan_date, plan_json)
           VALUES (%s, %s)
           ON CONFLICT (plan_date) DO UPDATE SET plan_json = EXCLUDED.plan_json, created_at = NOW()""",
        (date.today(), plan_text),
    )
    cur.close()
    conn.close()

    return plan_text


def run_cycle(num_holes: int = 5):
    """Run one full autonomous research cycle."""
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting research cycle...")

    holes = get_stale_rabbit_holes(limit=num_holes)
    if not holes:
        print("No active rabbit holes to research.")
        return

    print(f"Researching {len(holes)} rabbit holes...")
    for rh in holes:
        print(f"\n  Researching: {rh['name']} (priority: {rh['priority_score']})")
        research_rabbit_hole(rh)

    print("\nGenerating daily plan...")
    plan = build_daily_plan()
    print(f"\nDaily plan generated:\n{plan[:500]}...")

    print(f"\n[{datetime.now(timezone.utc).isoformat()}] Research cycle complete.")


if __name__ == "__main__":
    run_cycle()
