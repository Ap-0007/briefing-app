import json
from datetime import datetime
import db


def save(headlines: list[dict], ai_result: dict) -> int:
    ts = datetime.now().isoformat(timespec="seconds")
    return db.save_briefing(
        created_at=ts,
        headlines_json=json.dumps(headlines),
        ai_json=json.dumps(ai_result),
    )


def load(briefing_id: int) -> dict | None:
    row = db.get_briefing(briefing_id)
    if not row:
        return None
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "headlines": json.loads(row["headlines_json"]),
        "ai": json.loads(row["ai_json"]),
        "annotations": json.loads(row["annotations_json"]),
    }


def list_briefings() -> list[dict]:
    return db.get_briefing_list()


def save_annotation(briefing_id: int, story_title: str, note: str):
    row = db.get_briefing(briefing_id)
    if not row:
        return
    annotations = json.loads(row["annotations_json"])
    annotations[story_title] = note
    db.update_annotation(briefing_id, annotations)


def compute_diff(current: dict, previous: dict | None) -> dict[str, str]:
    """
    Returns {story_title: 'new'|'trending'|'gone'} tags.
    'gone' entries are previous stories not in current.
    """
    if not previous:
        return {}
    cur_titles = {s["title"] for s in current.get("stories", [])}
    prev_titles = {s["title"] for s in previous.get("stories", [])}

    result: dict[str, str] = {}
    for t in cur_titles:
        result[t] = "trending" if t in prev_titles else "new"
    for t in prev_titles - cur_titles:
        result[t] = "gone"
    return result
