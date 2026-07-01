# Provenance Guard — Planning

> Written before implementation. Sections marked **[YOUR VOICE]** are starting
> points only — replace with your own testing results and reasoning before
> submitting. Everything else reflects the actual implementation in this repo.

## 1. Detection Signals

**Signal 1 — LLM-based classification (Groq, llama-3.3-70b-versatile)**
- Measures: holistic semantic and stylistic coherence — does the passage
  "read like" typical AI output (hedged, evenly-paced, generically
  structured) or like a specific human voice?
- Output shape: float `ai_likelihood` in `[0, 1]` plus a one-sentence
  `rationale`, returned as JSON from the model.
- Blind spot: an LLM judge can be fooled by very short passages, by
  AI-generated text that's been lightly hand-edited, and it can't point to
  a measurable property — its "reasoning" is itself a generated artifact,
  not ground truth.

**Signal 2 — Stylometric heuristics (pure Python)**
- Measures: structural uniformity via three metrics — sentence-length
  variance, type-token ratio (vocabulary diversity), and punctuation
  density (semicolons/colons/comma-joins per sentence).
- Output shape: float `score` in `[0, 1]` (weighted combination of the
  three sub-metrics), plus the raw metric values.
- Blind spot: **[YOUR VOICE — confirm with your own testing]** on short
  passages (under ~5-6 sentences) these metrics are noisy and don't
  reliably separate AI from human text. Formal human writing (academic,
  legal) can also score similarly to AI text on this signal alone, which
  is why it's never used standalone.

**Combining them:** `confidence = 0.6 * llm_score + 0.4 * stylo_score`.
The LLM signal is weighted higher because it's a stronger standalone
predictor; stylometrics acts as a corroborating/disagreement check rather
than an equal partner.

## 2. Uncertainty Representation

A confidence score is the AI-likelihood estimate in `[0, 1]`, not a
probability of ground truth. Thresholds are **deliberately asymmetric**
because a false positive (accusing a human of using AI) is worse than a
false negative on a creative platform:

| Confidence range | Attribution | Label shown |
|---|---|---|
| `>= 0.75` | `likely_ai` | High-confidence AI |
| `0.31 – 0.74` | `uncertain` | Uncertain |
| `<= 0.30` | `likely_human` | High-confidence human |

A score of 0.6 means: mixed signals, lean-AI-but-not-enough-to-say-so —
shown to the reader as "uncertain," never rounded up to an accusation.

## 3. Transparency Label Variants (exact text)

**High-confidence AI:**
> "This work appears to be AI-generated. Our system detected strong signals of AI authorship in this content."

**High-confidence human:**
> "This work appears to be human-written. Our detection signals show no strong indication of AI generation."

**Uncertain:**
> "We're not confident enough to determine whether this was AI-generated or human-written. Signals were mixed - read with that uncertainty in mind."

## 4. Appeals Workflow

- Any creator can appeal a classification on their own `content_id` via
  `POST /appeal` with `content_id` and `creator_reasoning`.
- On receipt: the content's `status` flips to `under_review`,
  `appeal_reasoning` is stored on the record, and an `event: "appeal"`
  entry is appended to the audit log alongside the original decision
  (same `content_id`, so a reviewer can see both side by side).
- No automated re-classification — a human reviewer would open the
  appeal queue (in this project, `GET /log` filtered to `under_review`
  entries) and manually judge.
- **[YOUR VOICE]** what would a real reviewer UI need to show? (original
  text, both signal scores + rationale, appeal reasoning, at minimum.)

## 5. Anticipated Edge Cases

1. **Very short submissions** (a haiku, a two-line caption) — both
   signals become unreliable below a certain length; the stylometric
   signal in particular needs several sentences to compute variance
   meaningfully.
2. **[YOUR VOICE — add a second specific case from your own testing]**,
   e.g. "a non-native English speaker writing in a simplified, repetitive
   register that the stylometric signal reads as low-variance/low-TTR
   and therefore AI-like" — tie it to a concrete property of a signal,
   not a generic "detection isn't perfect."

## Architecture

```
                     POST /submit
                          |
                          v
              +-----------------------+
              |  Flask route: submit  |
              +-----------------------+
                     |         |
         raw text    |         |  raw text
                     v         v
            +-------------+  +--------------------+
            | llm_signal  |  | stylometric_signal  |
            | (Groq call) |  | (pure Python stats) |
            +-------------+  +--------------------+
                     |         |
              llm_score        stylo_score
                     \         /
                      v       v
                +-------------------+
                |  combine_scores   |  0.6*llm + 0.4*stylo
                +-------------------+
                          |
                    confidence (0-1)
                          v
                +-------------------+
                |     classify()    |  thresholds -> label text
                +-------------------+
                     |          |
              attribution     label
                     |          |
                     v          v
              +----------------------+
              |  append audit log    |
              +----------------------+
                          |
                          v
                 JSON response to caller


                    POST /appeal
                          |
                          v
              +-----------------------+
              |  Flask route: appeal  |
              +-----------------------+
                          |
        content_id looked up in content_store
                          |
                          v
           status -> "under_review"
           appeal_reasoning stored
                          |
                          v
              +----------------------+
              |  append audit log    |   event: "appeal"
              +----------------------+
                          |
                          v
                 JSON response to caller
```

Submission flow: text arrives at `/submit`, both signals run against the
raw text independently, their scores are combined into one confidence
value, that value is classified into a label, and the full result
(signals, confidence, label) is written to the audit log before being
returned to the caller. Appeal flow: a `content_id` is looked up, its
status is flipped and reasoning stored, and a second audit log entry is
appended that references the same `content_id` so the original decision
and the appeal sit side by side.

## AI Tool Plan

**M3 (submission endpoint + first signal):** Provide the "Detection
Signals" section above + the Architecture diagram. Ask for: Flask app
skeleton with a `POST /submit` stub, and the `llm_signal()` function.
Verify: call `llm_signal()` directly on 2-3 test strings and inspect the
JSON output shape before wiring into the route.

**M4 (second signal + confidence scoring):** Provide "Detection Signals"
+ "Uncertainty Representation" + diagram. Ask for: `stylometric_signal()`
and `combine_scores()`/`classify()`. Verify: run the 4 calibration inputs
(clear AI, clear human, 2 borderline) and confirm the resulting
confidence values land in the ranges the spec above predicts — correct
the weights/thresholds if they silently diverge from this document.

**M5 (production layer):** Provide "Transparency Label Variants" +
"Appeals Workflow" + diagram. Ask for: `classify()`'s label text mapping
and the `POST /appeal` route. Verify: hit `/submit` with inputs tuned to
land in all three confidence bands and confirm all three exact label
strings appear; hit `/appeal` and confirm `GET /log` shows
`status: under_review` with `appeal_reasoning` populated.