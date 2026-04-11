#!/usr/bin/env python3
"""
generate_data.py — Processes curated deprecation data and generates output files.

Reads from data/deprecations.json (seed + overrides), calculates urgency scores,
and outputs:
  - site/static/data/deprecation-timeline/latest.json  (all items with computed fields)
  - site/static/data/deprecation-timeline/stats.json    (summary counts)
  - site/static/data/deprecation-timeline/feed.xml      (RSS feed)
  - site/changelog.json                                  (change detection)
"""

import json
import hashlib
import os
import sys
from datetime import datetime, date, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
ROOT_DIR = SCRIPT_DIR.parent
DATA_DIR = ROOT_DIR / "data"
SITE_DIR = ROOT_DIR / "site"
OUTPUT_DIR = SITE_DIR / "static" / "data" / "deprecation-timeline"
PREVIOUS_STATE_FILE = SITE_DIR / "previous_state.json"

SITE_URL = "https://www.aguidetocloud.com"
TOOL_URL = f"{SITE_URL}/deprecation-timeline/"

CATEGORIES = {
    "sharepoint": {"name": "SharePoint", "emoji": "📂", "color": "#038387"},
    "teams": {"name": "Teams", "emoji": "💬", "color": "#6264A7"},
    "exchange": {"name": "Exchange", "emoji": "📧", "color": "#0078D4"},
    "entra": {"name": "Entra ID", "emoji": "🔐", "color": "#0078D4"},
    "azure": {"name": "Azure", "emoji": "☁️", "color": "#0089D6"},
    "office-apps": {"name": "Office Apps", "emoji": "📎", "color": "#D83B01"},
    "windows": {"name": "Windows", "emoji": "🪟", "color": "#0078D6"},
    "security": {"name": "Security", "emoji": "🛡️", "color": "#E3008C"},
}

TYPES = {
    "retirement": {"name": "Retirement", "emoji": "⛔", "description": "Feature/product completely removed"},
    "end-of-support": {"name": "End of Support", "emoji": "📅", "description": "No more patches or updates"},
    "deprecation": {"name": "Deprecation", "emoji": "⚠️", "description": "No new investment, will eventually retire"},
    "breaking-change": {"name": "Breaking Change", "emoji": "💥", "description": "Existing behaviour changes"},
    "api-sunset": {"name": "API Sunset", "emoji": "🔌", "description": "Specific API version discontinued"},
}


def calculate_urgency(deadline_str: str, action_required: bool = False) -> dict:
    """Calculate urgency level and days remaining from a deadline date string."""
    try:
        deadline = datetime.strptime(deadline_str, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {"level": "unknown", "days_remaining": None, "label": "Date TBD"}

    today = date.today()
    days = (deadline - today).days

    if days < 0:
        level = "passed"
        label = f"Passed ({abs(days)} days ago)"
    elif days == 0:
        level = "critical"
        label = "TODAY"
    elif days <= 30:
        level = "critical"
        label = f"{days} day{'s' if days != 1 else ''} remaining"
    elif days <= 90:
        level = "warning"
        label = f"{days} days remaining"
    elif days <= 180:
        level = "watch"
        label = f"{days} days remaining"
    else:
        level = "future"
        label = f"{days} days remaining"

    # Bump urgency if action required and not already critical/passed
    if action_required and level in ("watch", "future"):
        bump = {"future": "watch", "watch": "warning"}
        level = bump.get(level, level)

    return {"level": level, "days_remaining": days, "label": label}


def enrich_item(item: dict) -> dict:
    """Add computed fields to a deprecation item."""
    urgency = calculate_urgency(item.get("deadline"), item.get("action_required", False))
    item["urgency"] = urgency["level"]
    item["urgency_label"] = urgency["label"]
    item["days_remaining"] = urgency["days_remaining"]

    # Category metadata
    cat_key = item.get("category", "")
    cat_info = CATEGORIES.get(cat_key, {})
    item["category_name"] = cat_info.get("name", cat_key.title())
    item["category_emoji"] = cat_info.get("emoji", "📦")
    item["category_color"] = cat_info.get("color", "#666")

    # Type metadata
    type_key = item.get("type", "")
    type_info = TYPES.get(type_key, {})
    item["type_name"] = type_info.get("name", type_key.replace("-", " ").title())
    item["type_emoji"] = type_info.get("emoji", "ℹ️")

    # Status
    if item.get("urgency") == "passed":
        item["status"] = "passed"
    else:
        item["status"] = "active"

    return item


def compute_stats(items: list) -> dict:
    """Compute summary statistics from enriched items."""
    active = [i for i in items if i["status"] == "active"]
    passed = [i for i in items if i["status"] == "passed"]

    urgency_counts = {}
    for item in active:
        u = item["urgency"]
        urgency_counts[u] = urgency_counts.get(u, 0) + 1

    category_counts = {}
    for item in active:
        c = item["category"]
        category_counts[c] = category_counts.get(c, 0) + 1

    type_counts = {}
    for item in active:
        t = item["type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    # Next upcoming deadline
    upcoming = sorted(
        [i for i in active if i["days_remaining"] is not None and i["days_remaining"] >= 0],
        key=lambda x: x["days_remaining"]
    )
    next_deadline = None
    if upcoming:
        next_deadline = {
            "title": upcoming[0]["title"],
            "deadline": upcoming[0]["deadline"],
            "days_remaining": upcoming[0]["days_remaining"],
            "urgency": upcoming[0]["urgency"],
        }

    return {
        "generated": datetime.now(timezone.utc).isoformat(),
        "total": len(items),
        "active": len(active),
        "passed": len(passed),
        "action_required": len([i for i in active if i.get("action_required")]),
        "urgency": urgency_counts,
        "categories": category_counts,
        "types": type_counts,
        "next_deadline": next_deadline,
        "category_meta": CATEGORIES,
        "type_meta": TYPES,
    }


def generate_rss(items: list) -> str:
    """Generate RSS feed XML for active deprecation items."""
    active = sorted(
        [i for i in items if i["status"] == "active"],
        key=lambda x: x.get("deadline", "9999-12-31")
    )[:50]

    rss_items = []
    for item in active:
        urgency_emoji = {"critical": "🔴", "warning": "🟠", "watch": "🟡", "future": "🟢"}.get(
            item["urgency"], "⚪"
        )
        desc = (
            f'{urgency_emoji} {item["urgency_label"]} — '
            f'{item.get("description", "")}\n\n'
            f'Migration: {item.get("migration_path", "See official docs")}'
        )
        link = item.get("official_url") or TOOL_URL
        pub_date = ""
        if item.get("announced_date"):
            try:
                d = datetime.strptime(item["announced_date"], "%Y-%m-%d")
                pub_date = f"<pubDate>{d.strftime('%a, %d %b %Y 00:00:00 GMT')}</pubDate>"
            except ValueError:
                pass

        rss_items.append(f"""    <item>
      <title>{item["type_emoji"]} {item["title"]} — {item.get("deadline", "TBD")}</title>
      <link>{link}</link>
      <description><![CDATA[{desc}]]></description>
      <category>{item["category_name"]}</category>
      <guid isPermaLink="false">aguidetocloud-dep-{item["id"]}</guid>
      {pub_date}
    </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>M365 Deprecation Timeline — A Guide to Cloud</title>
    <link>{TOOL_URL}</link>
    <description>Microsoft 365 deprecation and retirement timeline — urgency-scored, filterable, with migration guidance.</description>
    <language>en</language>
    <lastBuildDate>{datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')}</lastBuildDate>
    <atom:link href="{SITE_URL}/data/deprecation-timeline/feed.xml" rel="self" type="application/rss+xml"/>
{chr(10).join(rss_items)}
  </channel>
</rss>"""


def detect_changes(items: list) -> list:
    """Compare current items with previous state to detect changes."""
    changes = []

    if not PREVIOUS_STATE_FILE.exists():
        return [{"type": "initial", "message": f"Initial load with {len(items)} items"}]

    try:
        with open(PREVIOUS_STATE_FILE, "r") as f:
            prev_state = json.load(f)
    except (json.JSONDecodeError, IOError):
        return [{"type": "error", "message": "Could not read previous state"}]

    prev_items = {i["id"]: i for i in prev_state.get("items", [])}
    curr_items = {i["id"]: i for i in items}

    # New items
    for item_id in set(curr_items) - set(prev_items):
        changes.append({
            "type": "added",
            "id": item_id,
            "title": curr_items[item_id]["title"],
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    # Removed items
    for item_id in set(prev_items) - set(curr_items):
        changes.append({
            "type": "removed",
            "id": item_id,
            "title": prev_items[item_id]["title"],
            "timestamp": datetime.now(timezone.utc).isoformat()
        })

    # Changed deadlines
    for item_id in set(curr_items) & set(prev_items):
        if curr_items[item_id].get("deadline") != prev_items[item_id].get("deadline"):
            changes.append({
                "type": "deadline_changed",
                "id": item_id,
                "title": curr_items[item_id]["title"],
                "old_deadline": prev_items[item_id].get("deadline"),
                "new_deadline": curr_items[item_id].get("deadline"),
                "timestamp": datetime.now(timezone.utc).isoformat()
            })

    return changes


def main():
    print("=== M365 Deprecation Timeline — Data Generator ===")

    # Load seed data
    seed_file = DATA_DIR / "deprecations.json"
    if not seed_file.exists():
        print(f"ERROR: {seed_file} not found")
        sys.exit(1)

    with open(seed_file, "r", encoding="utf-8") as f:
        seed_data = json.load(f)

    items = seed_data.get("items", [])
    print(f"Loaded {len(items)} items from seed data")

    # Load overrides
    overrides_file = DATA_DIR / "overrides.json"
    if overrides_file.exists():
        with open(overrides_file, "r", encoding="utf-8") as f:
            overrides = json.load(f)
        override_items = {o["id"]: o for o in overrides.get("items", [])}
        # Merge: overrides replace matching items or add new ones
        existing_ids = {i["id"] for i in items}
        for oid, oitem in override_items.items():
            if oid in existing_ids:
                items = [oitem if i["id"] == oid else i for i in items]
                print(f"  Override: {oid}")
            else:
                items.append(oitem)
                print(f"  Added: {oid}")

    # Enrich all items
    items = [enrich_item(item) for item in items]

    # Sort: active first (by days_remaining asc), then passed (by days_remaining desc)
    active = sorted(
        [i for i in items if i["status"] == "active"],
        key=lambda x: x["days_remaining"] if x["days_remaining"] is not None else 99999
    )
    passed = sorted(
        [i for i in items if i["status"] == "passed"],
        key=lambda x: x["days_remaining"] if x["days_remaining"] is not None else -99999,
        reverse=True
    )
    items = active + passed

    # Detect changes
    changes = detect_changes(items)
    if changes:
        print(f"Changes detected: {len(changes)}")
        for c in changes[:5]:
            print(f"  {c['type']}: {c.get('title', c.get('message', ''))}")

    # Compute stats
    stats = compute_stats(items)
    print(f"Stats: {stats['active']} active, {stats['passed']} passed, "
          f"{stats.get('action_required', 0)} action required")

    # Generate RSS
    rss = generate_rss(items)

    # Ensure output directories
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Write latest.json
    output = {
        "metadata": {
            "generated": datetime.now(timezone.utc).isoformat(),
            "total_items": len(items),
            "version": seed_data.get("metadata", {}).get("version", 1),
        },
        "items": items,
    }
    with open(OUTPUT_DIR / "latest.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Wrote {OUTPUT_DIR / 'latest.json'}")

    # Write stats.json
    with open(OUTPUT_DIR / "stats.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, ensure_ascii=False)
    print(f"Wrote {OUTPUT_DIR / 'stats.json'}")

    # Write RSS feed
    with open(OUTPUT_DIR / "feed.xml", "w", encoding="utf-8") as f:
        f.write(rss)
    print(f"Wrote {OUTPUT_DIR / 'feed.xml'}")

    # Save current state for change detection
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    with open(PREVIOUS_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"items": items}, f, ensure_ascii=False)

    # Write changelog
    changelog_file = SITE_DIR / "changelog.json"
    existing_changelog = []
    if changelog_file.exists():
        try:
            with open(changelog_file, "r") as f:
                existing_changelog = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    existing_changelog = changes + existing_changelog
    existing_changelog = existing_changelog[:200]  # Keep last 200 entries
    with open(changelog_file, "w", encoding="utf-8") as f:
        json.dump(existing_changelog, f, indent=2, ensure_ascii=False)

    print("\n✅ Data generation complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
