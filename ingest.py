"""Ingest ChatGPT conversations.json into Postgres and extract rabbit holes."""

import json
import sys
from datetime import datetime, timezone

from db import execute, execute_one, execute_batch, get_conn
from psycopg2.extras import RealDictCursor
from services.akash import classify_conversations


def parse_conversations(filepath: str) -> list[dict]:
    """Parse conversations.json, flatten message trees, return structured data."""
    with open(filepath) as f:
        raw = json.load(f)

    conversations = []
    for conv in raw:
        conv_id = conv.get("conversation_id") or conv.get("id", "")
        title = conv.get("title") or "Untitled"
        created_at = _ts_to_dt(conv.get("create_time"))
        updated_at = _ts_to_dt(conv.get("update_time"))
        model_slug = conv.get("default_model_slug")

        # Flatten message tree
        messages = []
        mapping = conv.get("mapping", {})
        for node_id, node in mapping.items():
            msg = node.get("message")
            if not msg:
                continue
            role = msg.get("author", {}).get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content", {})
            parts = content.get("parts", [])
            text_parts = [p for p in parts if isinstance(p, str) and p.strip()]
            if not text_parts:
                continue
            text = "\n".join(text_parts)
            msg_created = _ts_to_dt(msg.get("create_time"))
            messages.append({
                "id": node_id,
                "role": role,
                "content": text,
                "created_at": msg_created,
            })

        # Sort messages by created_at (None last)
        messages.sort(key=lambda m: m["created_at"] or datetime.min.replace(tzinfo=timezone.utc))

        conversations.append({
            "id": conv_id,
            "title": title,
            "created_at": created_at,
            "updated_at": updated_at,
            "model_slug": model_slug,
            "messages": messages,
            "message_count": len(messages),
        })

    return conversations


def parse_conversations_bytes(data: bytes) -> list[dict]:
    """Parse conversations.json from raw bytes."""
    raw = json.loads(data)
    conversations = []
    for conv in raw:
        conv_id = conv.get("conversation_id") or conv.get("id", "")
        title = conv.get("title") or "Untitled"
        created_at = _ts_to_dt(conv.get("create_time"))
        updated_at = _ts_to_dt(conv.get("update_time"))
        model_slug = conv.get("default_model_slug")

        messages = []
        mapping = conv.get("mapping", {})
        for node_id, node in mapping.items():
            msg = node.get("message")
            if not msg:
                continue
            role = msg.get("author", {}).get("role")
            if role not in ("user", "assistant"):
                continue
            content = msg.get("content", {})
            parts = content.get("parts", [])
            text_parts = [p for p in parts if isinstance(p, str) and p.strip()]
            if not text_parts:
                continue
            text = "\n".join(text_parts)
            msg_created = _ts_to_dt(msg.get("create_time"))
            messages.append({
                "id": node_id,
                "role": role,
                "content": text,
                "created_at": msg_created,
            })

        messages.sort(key=lambda m: m["created_at"] or datetime.min.replace(tzinfo=timezone.utc))

        conversations.append({
            "id": conv_id,
            "title": title,
            "created_at": created_at,
            "updated_at": updated_at,
            "model_slug": model_slug,
            "messages": messages,
            "message_count": len(messages),
        })

    return conversations


def _ts_to_dt(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _normalize_rh_name(name: str) -> str:
    """Normalize for dedup: 'Language Learning & Practice' == 'Language Learning and Practice'."""
    if not name:
        return ""
    s = name.lower().strip().replace("&", "and")
    return " ".join(s.split())


def insert_conversations(conversations: list[dict], user_id: str = None):
    """Insert conversations and messages into Postgres."""
    prefix = f"{user_id}:" if user_id else ""
    conv_params = [
        (prefix + c["id"], user_id, c["title"], c["created_at"], c["updated_at"], c["message_count"], c["model_slug"])
        for c in conversations
    ]
    execute_batch(
        """INSERT INTO conversations (id, user_id, title, created_at, updated_at, message_count, model_slug)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING""",
        conv_params,
    )
    print(f"Inserted {len(conv_params)} conversations.")

    msg_params = []
    for c in conversations:
        cid = prefix + c["id"]
        for m in c["messages"]:
            msg_params.append((prefix + m["id"], cid, m["role"], m["content"], m["created_at"]))

    execute_batch(
        """INSERT INTO messages (id, conversation_id, role, content, created_at)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING""",
        msg_params,
    )
    print(f"Inserted {len(msg_params)} messages.")

    # Update conversation IDs in parsed data so rabbit hole extraction uses prefixed IDs
    for c in conversations:
        c["_prefixed_id"] = prefix + c["id"]


def extract_rabbit_holes(conversations: list[dict], user_id: str = None):
    """Use DeepSeek to classify conversations into rabbit holes."""
    prefix = f"{user_id}:" if user_id else ""
    # Filter out trivial conversations (< 4 messages)
    substantive = [c for c in conversations if c["message_count"] >= 4]
    print(f"Classifying {len(substantive)} substantive conversations into rabbit holes...")

    # Build summaries for DeepSeek (use original IDs for the LLM, map to prefixed later)
    summaries = []
    for c in substantive:
        first_msgs = " | ".join(
            f"[{m['role']}]: {m['content'][:150]}"
            for m in c["messages"][:3]
        )
        summaries.append({
            "id": c["id"],
            "title": c["title"],
            "message_count": c["message_count"],
            "first_messages": first_msgs,
            "created_at": str(c["created_at"]),
        })

    # Process in batches of 30 to stay within context limits
    batch_size = 30
    all_holes = []
    for i in range(0, len(summaries), batch_size):
        batch = summaries[i : i + batch_size]
        print(f"  Batch {i // batch_size + 1}: classifying {len(batch)} conversations...")
        holes = classify_conversations(batch)
        all_holes.extend(holes)

    # Merge rabbit holes by normalized name (so "X & Y" and "X and Y" become one)
    merged = {}
    for rh in all_holes:
        key = _normalize_rh_name(rh["name"])
        if key in merged:
            merged[key]["conversation_ids"].extend(rh.get("conversation_ids", []))
            merged[key]["conversation_ids"] = list(set(merged[key]["conversation_ids"]))
        else:
            merged[key] = rh

    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()

    # Load existing rabbit holes for this user so we reuse instead of duplicating
    existing_by_norm = {}
    if user_id:
        cur.execute("SELECT id, name FROM rabbit_holes WHERE user_id = %s", (user_id,))
        for row in cur.fetchall():
            existing_by_norm[_normalize_rh_name(row["name"])] = row["id"]

    created = 0
    for rh in merged.values():
        raw_conv_ids = rh.get("conversation_ids", [])
        conv_ids = [prefix + cid for cid in raw_conv_ids]
        conv_data = [c for c in conversations if c["id"] in raw_conv_ids]
        total_msgs = sum(c["message_count"] for c in conv_data)
        recency_bonus = 0
        if conv_data:
            latest = max((c["updated_at"] or c["created_at"] or datetime.min.replace(tzinfo=timezone.utc)) for c in conv_data)
            days_ago = (datetime.now(timezone.utc) - latest).days
            recency_bonus = max(0, 10 - days_ago * 0.1)
        priority = len(conv_ids) * 2 + total_msgs * 0.1 + recency_bonus
        norm = _normalize_rh_name(rh["name"])

        if norm in existing_by_norm:
            rh_id = existing_by_norm[norm]
        else:
            cur.execute(
                """INSERT INTO rabbit_holes (user_id, name, description, priority_score)
                   VALUES (%s, %s, %s, %s) RETURNING id""",
                (user_id, rh["name"], rh.get("description", ""), round(priority, 2)),
            )
            rh_id = cur.fetchone()[0]
            existing_by_norm[norm] = rh_id
            created += 1

        for cid in conv_ids:
            cur.execute(
                """INSERT INTO rabbit_hole_conversations (rabbit_hole_id, conversation_id)
                   SELECT %s, %s WHERE EXISTS (SELECT 1 FROM conversations WHERE id = %s)
                   ON CONFLICT DO NOTHING""",
                (rh_id, cid, cid),
            )

    cur.close()
    conn.close()
    print(f"Created {created} new rabbit holes, linked to {len(merged)} topics.")


def run(filepath: str, user_id: str = None):
    print(f"Parsing {filepath}...")
    conversations = parse_conversations(filepath)
    print(f"Found {len(conversations)} conversations with {sum(c['message_count'] for c in conversations)} total messages.")

    print("Inserting into database...")
    insert_conversations(conversations, user_id=user_id)

    print("Extracting rabbit holes via DeepSeek...")
    extract_rabbit_holes(conversations, user_id=user_id)

    print("Ingestion complete.")


def run_from_bytes(data: bytes, user_id: str):
    """Run ingestion from raw bytes (for upload endpoint)."""
    print(f"Parsing uploaded file for user {user_id}...")
    conversations = parse_conversations_bytes(data)
    print(f"Found {len(conversations)} conversations with {sum(c['message_count'] for c in conversations)} total messages.")

    print("Inserting into database...")
    insert_conversations(conversations, user_id=user_id)

    print("Extracting rabbit holes via DeepSeek...")
    extract_rabbit_holes(conversations, user_id=user_id)

    print("Ingestion complete for user", user_id)


def deduplicate_rabbit_holes(user_id: str = None):
    """Merge duplicate rabbit holes (same normalized name) for a user or all users."""
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(
        "SELECT id, user_id, name FROM rabbit_holes" + (" WHERE user_id = %s" if user_id else ""),
        (user_id,) if user_id else None,
    )
    rows = cur.fetchall()
    by_key = {}
    for r in rows:
        key = (r["user_id"], _normalize_rh_name(r["name"]))
        by_key.setdefault(key, []).append(r)

    merged = 0
    for (uid, norm), group in by_key.items():
        if len(group) <= 1:
            continue
        keep = min(group, key=lambda r: r["id"])
        dupes = [r for r in group if r["id"] != keep["id"]]
        for d in dupes:
            cur.execute(
                """INSERT INTO rabbit_hole_conversations (rabbit_hole_id, conversation_id)
                   SELECT %s, conversation_id FROM rabbit_hole_conversations WHERE rabbit_hole_id = %s
                   ON CONFLICT DO NOTHING""",
                (keep["id"], d["id"]),
            )
            cur.execute("DELETE FROM rabbit_hole_conversations WHERE rabbit_hole_id = %s", (d["id"],))
            cur.execute("UPDATE insights SET rabbit_hole_id = %s WHERE rabbit_hole_id = %s", (keep["id"], d["id"]))
            cur.execute("UPDATE research_runs SET rabbit_hole_id = %s WHERE rabbit_hole_id = %s", (keep["id"], d["id"]))
            cur.execute("DELETE FROM rabbit_holes WHERE id = %s", (d["id"],))
            merged += 1
    conn.commit()
    cur.close()
    conn.close()
    print(f"Merged {merged} duplicate rabbit holes.")


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/Users/kian/Downloads/hackathon/conversations.json"
    run(path)
