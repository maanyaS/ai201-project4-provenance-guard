# Provenance Guard

A backend that classifies submitted text as likely AI-generated, likely
human-written, or uncertain — with a confidence score, a plain-language
transparency label, an appeals path, rate limiting, and a structured audit
log.

## Architecture Overview

`POST /submit` sends the raw text to two independent signals — an LLM
judgment call (Groq) and a stylometric heuristic (pure Python) — combines
their scores into a single confidence value, maps that value to one of
three transparency labels, writes the full decision to the audit log, and
returns the result. `POST /appeal` looks up a prior submission by
`content_id`, flips its status to `under_review`, stores the creator's
reasoning, and logs the appeal alongside the original decision. See
`planning.md` for the full diagram.

## Detection Signals

- **LLM-based (Groq, llama-3.3-70b-versatile):** asks the model to judge
  holistic semantic/stylistic coherence, returning an AI-likelihood score
  0-1 with a one-sentence rationale. Chosen because it captures things no
  simple statistic can — tone, genericness, argument structure. Misses:
  short passages, lightly-edited AI text, and it can't cite a measurable
  property, only its own generated judgment. Testing also surfaced a
  meta-cue blind spot: several placeholder test strings like "Rate limit
  test submission text here." scored `llm_score: 0.9` with the model's own
  rationale noting the text read as "characteristic of AI-generated text,
  particularly in test or placeholder scenarios" — i.e. it was detecting
  "this looks like throwaway test copy," not linguistic AI-vs-human
  markers specifically.
- **Stylometric heuristics (pure Python):** sentence-length variance,
  type-token ratio, and punctuation density, combined into a single
  structural-uniformity score. Chosen because it's genuinely independent
  of the LLM signal — no API call, and it measures *structure* rather
  than *meaning*. Misses: short passages. Across all four calibration
  inputs (3-4 sentences each), the stylometric score stayed in a narrow
  0.10-0.19 band regardless of whether the text was clearly AI-generated
  or clearly human-written — sentence-length variance and type-token
  ratio simply don't have enough sentences to compute a meaningful
  statistic at that length, so the signal contributed noise rather than a
  useful second opinion on short text.

## Confidence Scoring

`confidence = 0.6 * llm_score + 0.4 * stylo_score`. The LLM signal is
weighted higher as the stronger standalone predictor; stylometrics
corroborates or flags disagreement rather than voting equally.
Thresholds are asymmetric (`>=0.75` AI, `<=0.30` human, else uncertain)
because a false accusation of AI use is worse than a missed detection on
a creative platform.

**Validating the scores meant what I intended:**
Ran the 4 calibration inputs from the spec. The LLM signal correctly
separated AI vs. human (0.8 vs 0.2 on the clear examples), but the
stylometric signal scored low (0.10–0.19) across all four inputs,
including the clearly-AI one — because these passages are too short
(3-4 sentences) for sentence-length variance and type-token ratio to be
reliable, a risk flagged in `planning.md` before building. As a result,
even the clearest AI example landed in "uncertain" (0.5448) rather than
"likely_ai," since it never crossed the 0.75 threshold. This confirms the
asymmetric thresholds are doing their job (biased against false
accusations) but also shows the stylometric signal needs longer input to
pull its weight — see Known Limitations below.

**Two example submissions with different confidence scores:**

| Example | llm_score | stylo_score | confidence | attribution |
|---|---|---|---|---|
| Clearly AI-generated | 0.80 | 0.16 | 0.5448 | uncertain |
| Clearly human-written | 0.20 | 0.00 | 0.12 | likely_human |
| Borderline (formal human) | 0.70 | 0.11 | 0.4639 | uncertain |
| Borderline (lightly-edited AI) | 0.20 | 0.19 | 0.1979 | likely_human |

## Transparency Label

| Variant | Exact text shown |
|---|---|
| High-confidence AI | "This work appears to be AI-generated. Our system detected strong signals of AI authorship in this content." |
| High-confidence human | "This work appears to be human-written. Our detection signals show no strong indication of AI generation." |
| Uncertain | "We're not confident enough to determine whether this was AI-generated or human-written. Signals were mixed - read with that uncertainty in mind." |

## Appeals Workflow

`POST /appeal` with `content_id` and `creator_reasoning` sets that
content's status to `under_review`, stores the reasoning on the record,
and appends an audit log entry (`event: "appeal"`) that sits alongside
the original classification for the same `content_id`. No automated
re-classification — resolution is manual.

**Real test — request:**
```powershell
Invoke-RestMethod -Uri http://localhost:5000/appeal -Method Post -ContentType "application/json" -Body '{"content_id": "01e2d1fc-43b5-4d8f-b1ce-fcb11252df82", "creator_reasoning": "I wrote this myself."}'
```

**Response:**
```
content_id                            message
-----------                           -------
01e2d1fc-43b5-4d8f-b1ce-fcb11252df82  Appeal received and logged. A huma...
```

**Matching `/log` entry confirming the status change:**
```json
{
  "appeal_reasoning": "I wrote this myself.",
  "attribution": "uncertain",
  "confidence": 0.32,
  "content_id": "01e2d1fc-43b5-4d8f-b1ce-fcb11252df82",
  "creator_id": "test-user-1",
  "event": "appeal",
  "status": "under_review",
  "timestamp": "2026-07-01T05:57:27.668219+00:00"
}
```

## Rate Limiting

`10 per minute; 100 per day` on `POST /submit`, via Flask-Limiter with
in-memory storage. Reasoning: a working creator submitting their own
pieces rarely exceeds a handful of requests per minute even while
iterating; 10/minute comfortably covers that while still blocking a
script trying to flood the classifier. The 100/day ceiling caps
sustained abuse from a single IP without punishing a heavy single
session.

**Verifying the mechanism:** each `/submit` call makes a live Groq API
call, which adds a few seconds of latency per request. Firing 12
sequential requests at the real 10/minute limit took longer than 60
seconds wall-clock, so the per-minute window reset before the limit was
ever hit — the log shows two 12-request batches (`ratelimit-test`,
`ratelimit-test2`) that all returned `200`. To confirm the limiter itself
is enforcing correctly, I temporarily lowered the limit to `3 per minute`
and re-ran the same test:

```
200
200
200
429
429
429
```

Three requests succeeded, the rest were rejected with `429 Too Many
Requests` — confirming Flask-Limiter is correctly wired to the endpoint.
The limit was restored to the documented production value (`10 per
minute; 100 per day`) immediately after this test.

## Audit Log

Every submission and appeal writes a structured JSON entry (see
`storage.py` / `GET /log`) containing timestamp, content ID, attribution,
confidence, both individual signal scores + rationale, and status.

Sample entries pulled from `GET /log` (39 total entries recorded during
testing). Below: one classified submission, its matching appeal, and one
calibration test.

**Submission entry:**
```json
{
  "attribution": "uncertain",
  "confidence": 0.32,
  "content_id": "01e2d1fc-43b5-4d8f-b1ce-fcb11252df82",
  "creator_id": "test-user-1",
  "event": "submission",
  "llm_score": 0.2,
  "stylo_score": 0.5,
  "stylo_rationale": "Text too short for reliable stylometric analysis.",
  "status": "classified",
  "timestamp": "2026-07-01T05:55:20.693464+00:00"
}
```

**Matching appeal entry (same content_id, status updated):**
```json
{
  "appeal_reasoning": "I wrote this myself.",
  "attribution": "uncertain",
  "confidence": 0.32,
  "content_id": "01e2d1fc-43b5-4d8f-b1ce-fcb11252df82",
  "creator_id": "test-user-1",
  "event": "appeal",
  "status": "under_review",
  "timestamp": "2026-07-01T05:57:27.668219+00:00"
}
```

**Calibration test entry with full signal breakdown:**
```json
{
  "attribution": "uncertain",
  "confidence": 0.5448,
  "content_id": "55d39155-fe76-4199-b4c7-1b7469a2f1bc",
  "creator_id": "calibration-ai",
  "event": "submission",
  "llm_score": 0.8,
  "llm_rationale": "The text's formal tone, repetitive sentence structure, and reliance on buzzwords like 'paradigm shift' and 'stakeholders' are characteristic of AI-generated content.",
  "stylo_score": 0.16194444444444442,
  "stylo_rationale": "sentence-length variance=29.6, type-token ratio=0.88, punctuation density=0.67",
  "status": "classified",
  "timestamp": "2026-07-01T06:15:02.607193+00:00"
}
```

## Known Limitations

Short passages (3-5 sentences) make the stylometric signal unreliable. In
testing, all four calibration texts — including a clearly AI-generated
paragraph — scored between 0.10 and 0.19 on the stylometric signal,
regardless of whether the LLM signal correctly identified them as AI
(0.8) or human (0.2). Sentence-length variance and type-token ratio need
more sentences to be statistically meaningful, so on short excerpts the
stylometric signal contributes noise rather than a useful second
opinion, and the system leans almost entirely on the LLM signal in
practice for short text. A lightly-edited AI passage (calibration example
4) was misclassified as "likely_human" (confidence 0.1979) for this
reason — the LLM signal alone gave it 0.2, likely because light editing
removed the more obvious tells the LLM judge looks for.

A second, related limitation: the LLM signal isn't purely evaluating
linguistic style. Several placeholder test submissions ("Rate limit test
submission text here.") scored high (`llm_score: 0.9`) with the model's
own rationale explicitly citing that the text "lacks coherence, context,
and meaningful content, which is a common characteristic of AI-generated
text, particularly in test or placeholder scenarios." That means the
signal is partly reacting to "this reads like throwaway/test content"
rather than exclusively to AI-vs-human authorship markers — a real
passage that happens to be short and generic (not AI-generated, just
low-effort) could get flagged for the wrong reason.

## Spec Reflection

Writing out the exact label text and the asymmetric thresholds in
`planning.md` before touching the endpoint code paid off directly: when
implementing `classify()`, the three label strings and the `0.75` /
`0.30` cutoffs were already decided, so there was no ad-hoc decision-making
happening inside the route handler — the spec did that thinking up front.

Where implementation diverged from the plan: `planning.md` didn't specify
exact signal weights beyond noting the LLM signal would be "weighted
higher." The 60/40 split was a reasonable-sounding number picked before
testing. Running the calibration inputs showed the stylometric signal was
contributing very little useful separation on short text (staying in a
narrow 0.10-0.19 band regardless of ground truth), which the spec's own
"anticipated edge cases" section had actually predicted as a risk. Given
more time, the honest fix would be to make the stylometric weight
adaptive to text length rather than fixed — noted here rather than
implemented, since testing this thoroughly enough to trust a new
weighting scheme wasn't feasible under the project's time constraints.

## AI Usage

1. Directed an AI tool to scaffold the Flask app, the two signal
   functions, and the storage layer from the `planning.md` spec
   (detection signals section + architecture diagram). It produced a
   working `llm_signal()` / `stylometric_signal()` split and a JSON-file
   based `storage.py`. I reviewed and kept the JSON-parsing error handling
   in `llm_signal()` (falling back to a neutral 0.5 score if the Groq call
   fails) rather than letting a network error crash the endpoint — that
   fallback turned out to matter in practice during testing.

2. When testing rate limiting from PowerShell, an AI tool's first
   suggested test loop used bash-style `curl` flags and later a
   backtick-escaped string interpolation (`` "$_`: $($r.StatusCode)" ``)
   that PowerShell's parser rejected as an unexpected token. I had it
   simplify to a plain `try/catch` with `Write-Output $r.StatusCode` and
   add `-UseBasicParsing` to suppress a script-execution security prompt
   that was silently blocking the loop — the working version is what's
   documented in the Rate Limiting section above.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env        # add your GROQ_API_KEY
python app.py
```

## Portfolio Walkthrough

<div>
    <a href="https://www.loom.com/share/d912b97650274d358c8bef43b089c8a4">
      <p>Building project guidance - Claude - 1 July 2026 - Watch Video</p>
    </a>
    <a href="https://www.loom.com/share/d912b97650274d358c8bef43b089c8a4">
      <img style="max-width:300px;" src="https://cdn.loom.com/sessions/thumbnails/d912b97650274d358c8bef43b089c8a4-0b1b701030111264-full-play.gif#t=0.1">
    </a>
  </div>