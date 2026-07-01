"""
storage.py

Very small, dependency-free persistence layer. Uses a single JSON file on
disk as a stand-in for a database. Good enough for a project of this scope;
swap for SQLite/Postgres if you productionize this for real.

Data model:

content_store: {
    content_id: {
        "content_id": str,
        "creator_id": str,
        "text": str,
        "timestamp": iso8601 str,
        "llm_score": float | None,
        "stylo_score": float | None,
        "confidence": float | None,
        "attribution": str | None,        # "likely_ai" | "likely_human" | "uncertain"
        "label": str | None,              # the exact transparency label text
        "status": str,                    # "classified" | "under_review"
        "appeal_reasoning": str | None,
    }
}

audit_log: list of dict entries, append-only, most-recent-last.
"""

import json
import os
import threading

DATA_FILE = os.path.join(os.path.dirname(__file__), "data.json")
_lock = threading.Lock()


def _empty_state():
    return {"content_store": {}, "audit_log": []}


def _load():
    if not os.path.exists(DATA_FILE):
        return _empty_state()
    with open(DATA_FILE, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return _empty_state()


def _save(state):
    with open(DATA_FILE, "w") as f:
        json.dump(state, f, indent=2)


def save_content(record: dict):
    with _lock:
        state = _load()
        state["content_store"][record["content_id"]] = record
        _save(state)


def get_content(content_id: str):
    with _lock:
        state = _load()
        return state["content_store"].get(content_id)


def update_content(content_id: str, updates: dict):
    with _lock:
        state = _load()
        record = state["content_store"].get(content_id)
        if record is None:
            return None
        record.update(updates)
        state["content_store"][content_id] = record
        _save(state)
        return record


def append_log(entry: dict):
    with _lock:
        state = _load()
        state["audit_log"].append(entry)
        _save(state)


def get_log(limit: int = 50):
    with _lock:
        state = _load()
        return state["audit_log"][-limit:]