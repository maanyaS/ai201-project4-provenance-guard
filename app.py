"""
app.py

Provenance Guard backend.

Flow:
  POST /submit  -> llm_signal + stylometric_signal -> combined confidence
                 -> transparency label -> audit log entry -> JSON response
  POST /appeal  -> status -> "under_review" -> audit log entry -> JSON response
  GET  /log     -> most recent audit log entries (for grading / debugging)

Design decision (documented further in planning.md / README):
  A false positive (calling a human's work "AI-generated") is worse than a
  false negative on a creative platform, so the thresholds below are
  deliberately asymmetric: it takes a HIGH score to call something
  "likely AI", but only a moderate-low score to call something
  "likely human". Everything in between is "uncertain" rather than
  forced into a binary call.
"""

import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import storage
from signals import llm_signal, stylometric_signal

app = Flask(__name__)

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)

# --- Confidence scoring ------------------------------------------------

# Signal weights: the LLM signal is weighted higher because it captures
# holistic semantic/stylistic coherence, which is a stronger standalone
# predictor than structural stats alone; stylometrics acts as a
# corroborating / disagreement check.
LLM_WEIGHT = 0.6
STYLO_WEIGHT = 0.4

# Asymmetric thresholds - see module docstring.
AI_THRESHOLD = 0.75
HUMAN_THRESHOLD = 0.30


def combine_scores(llm_score: float, stylo_score: float) -> float:
    return round((LLM_WEIGHT * llm_score) + (STYLO_WEIGHT * stylo_score), 4)


def classify(confidence: float):
    """Returns (attribution, label_text)."""
    if confidence >= AI_THRESHOLD:
        attribution = "likely_ai"
        label = (
            "This work appears to be AI-generated. Our system detected "
            "strong signals of AI authorship in this content."
        )
    elif confidence <= HUMAN_THRESHOLD:
        attribution = "likely_human"
        label = (
            "This work appears to be human-written. Our detection signals "
            "show no strong indication of AI generation."
        )
    else:
        attribution = "uncertain"
        label = (
            "We're not confident enough to determine whether this was "
            "AI-generated or human-written. Signals were mixed - read "
            "with that uncertainty in mind."
        )
    return attribution, label


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# --- Routes --------------------------------------------------------------


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute; 100 per day")
def submit():
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    creator_id = data.get("creator_id")

    if not text or not creator_id:
        return jsonify({"error": "text and creator_id are required"}), 400

    content_id = str(uuid.uuid4())

    llm_result = llm_signal(text)
    stylo_result = stylometric_signal(text)

    confidence = combine_scores(llm_result["score"], stylo_result["score"])
    attribution, label = classify(confidence)

    record = {
        "content_id": content_id,
        "creator_id": creator_id,
        "text": text,
        "timestamp": now_iso(),
        "llm_score": llm_result["score"],
        "stylo_score": stylo_result["score"],
        "confidence": confidence,
        "attribution": attribution,
        "label": label,
        "status": "classified",
        "appeal_reasoning": None,
    }
    storage.save_content(record)

    storage.append_log({
        "content_id": content_id,
        "creator_id": creator_id,
        "timestamp": record["timestamp"],
        "attribution": attribution,
        "confidence": confidence,
        "llm_score": llm_result["score"],
        "llm_rationale": llm_result.get("rationale", ""),
        "stylo_score": stylo_result["score"],
        "stylo_rationale": stylo_result.get("rationale", ""),
        "status": "classified",
        "event": "submission",
    })

    return jsonify({
        "content_id": content_id,
        "attribution": attribution,
        "confidence": confidence,
        "label": label,
        "signals": {
            "llm_score": llm_result["score"],
            "stylo_score": stylo_result["score"],
        },
    })


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = data.get("content_id")
    creator_reasoning = data.get("creator_reasoning")

    if not content_id or not creator_reasoning:
        return jsonify({"error": "content_id and creator_reasoning are required"}), 400

    record = storage.get_content(content_id)
    if record is None:
        return jsonify({"error": "content_id not found"}), 404

    updated = storage.update_content(content_id, {
        "status": "under_review",
        "appeal_reasoning": creator_reasoning,
    })

    storage.append_log({
        "content_id": content_id,
        "creator_id": record["creator_id"],
        "timestamp": now_iso(),
        "attribution": record["attribution"],
        "confidence": record["confidence"],
        "status": "under_review",
        "appeal_reasoning": creator_reasoning,
        "event": "appeal",
    })

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": "Appeal received and logged. A human reviewer will examine this classification.",
    })


@app.route("/log", methods=["GET"])
def get_log():
    return jsonify({"entries": storage.get_log()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)