"""Ingest ChatGPT conversations.json into Postgres and extract rabbit holes."""

import json
import sys
from datetime import datetime, timezone

from db import execute, execute_one, execute_batch, get_conn
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


def insert_conversations(conversations: list[dict], user_id: str = None):
    """Insert conversations and messages into Postgres."""
    conv_params = [
        (c["id"], user_id, c["title"], c["created_at"], c["updated_at"], c["message_count"], c["model_slug"])
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
        for m in c["messages"]:
            msg_params.append((m["id"], c["id"], m["role"], m["content"], m["created_at"]))

    execute_batch(
        """INSERT INTO messages (id, conversation_id, role, content, created_at)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (id) DO NOTHING""",
        msg_params,
    )
    print(f"Inserted {len(msg_params)} messages.")


def extract_rabbit_holes(conversations: list[dict], user_id: str = None):
    """Use DeepSeek to classify conversations into rabbit holes."""
    # Filter out trivial conversations (< 4 messages)
    substantive = [c for c in conversations if c["message_count"] >= 4]
    print(f"Classifying {len(substantive)} substantive conversations into rabbit holes...")

    # Build summaries for DeepSeek
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

    # Merge rabbit holes with the same name (case-insensitive)
    merged = {}
    for rh in all_holes:
        key = rh["name"].lower().strip()
        if key in merged:
            merged[key]["conversation_ids"].extend(rh.get("conversation_ids", []))
            merged[key]["conversation_ids"] = list(set(merged[key]["conversation_ids"]))
        else:
            merged[key] = rh

    # Insert rabbit holes and link conversations
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()

    for rh in merged.values():
        conv_ids = rh.get("conversation_ids", [])
        # Compute priority: more conversations + more messages = higher priority
        conv_data = [c for c in conversations if c["id"] in conv_ids]
        total_msgs = sum(c["message_count"] for c in conv_data)
        recency_bonus = 0
        if conv_data:
            latest = max((c["updated_at"] or c["created_at"] or datetime.min.replace(tzinfo=timezone.utc)) for c in conv_data)
            days_ago = (datetime.now(timezone.utc) - latest).days
            recency_bonus = max(0, 10 - days_ago * 0.1)
        priority = len(conv_ids) * 2 + total_msgs * 0.1 + recency_bonus

        cur.execute(
            """INSERT INTO rabbit_holes (user_id, name, description, priority_score)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (user_id, rh["name"], rh.get("description", ""), round(priority, 2)),
        )
        rh_id = cur.fetchone()[0]

        for cid in conv_ids:
            # Only link if conversation actually exists (DeepSeek may hallucinate IDs)
            cur.execute(
                """INSERT INTO rabbit_hole_conversations (rabbit_hole_id, conversation_id)
                   SELECT %s, %s WHERE EXISTS (SELECT 1 FROM conversations WHERE id = %s)
                   ON CONFLICT DO NOTHING""",
                (rh_id, cid, cid),
            )

    cur.close()
    conn.close()
    print(f"Created {len(merged)} rabbit holes.")


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


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/Users/kian/Downloads/hackathon/conversations.json"
    run(path)
