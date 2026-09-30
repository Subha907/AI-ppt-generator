"""Agent 4 (Visual Reviewer) generation layer.

Assesses the rendered slide preview against visual-quality rules and returns a
review-report dict. Two providers:
  * "stub": deterministic PASS report (no model) - lets the pipeline run.
  * "nova": Amazon Nova Pro vision via the Converse API - sends the preview PNG
    plus a compact layout summary and asks for the review-report JSON.

The reviewer only REPORTS; it never edits the slide.
"""
from __future__ import annotations

import json
import re

from .aws import bedrock_runtime_client
from .config import get_settings

REVIEW_SYSTEM_PROMPT = """You are the Visual Reviewer agent in an automated AWS
architecture slide pipeline. You are shown a rendered slide image plus a summary
of its objects. Assess ONLY visual quality and return STRICT JSON.

Check for these issues (rule codes):
- TEXT_CLIPPED: a label is cut off or overflows its box.
- LOW_CONTRAST: text hard to read against its background.
- OVERLAP: two objects visually collide.
- EXCESSIVE_CROSSINGS: connector lines cross excessively / are hard to follow.
- INCONSISTENT_SPACING: uneven gaps between peer objects.
- UNREADABLE_LABEL: text too small or truncated to read.
- OUT_OF_BOUNDS: an object sits outside its container or the slide.
- EMPTY_GROUP: a labeled container has no meaningful content.

Hard rules:
- Output ONLY a single JSON object. No markdown, no prose, no code fences.
- decision is "PASS" if the slide is clean enough to publish, else "REVISE".
- score is 0-100 (higher = better).
- Each issue MUST cite the affected objectIds (use the ids from the summary),
  a severity (LOW|MEDIUM|HIGH), a rule code, and a recommendedAction with a
  "type" (RESIZE|MOVE|RESPACE|RELABEL|REGROUP|RECOLOR|REROUTE) and optional
  numeric "delta".
- Be specific and conservative: only report genuine problems.

Required JSON shape:
{
  "schemaVersion": "1.0",
  "projectId": string,
  "jobId": string,
  "decision": "PASS" | "REVISE",
  "score": integer,
  "issues": [
    {"issueId": string, "severity": "LOW|MEDIUM|HIGH", "rule": string,
     "objectIds": [string], "recommendedAction": {"type": string, "delta": {}}}
  ],
  "summary": string
}
"""


class ReviewError(RuntimeError):
    pass


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(text[start : end + 1])
        raise ReviewError("Model did not return valid JSON")


def _layout_summary(layout: dict) -> str:
    """A compact text summary of objects/connectors to ground the reviewer."""
    objs = layout.get("objects", [])
    lines = [f"slide: {layout['slide']['widthIn']} x {layout['slide']['heightIn']} in"]
    lines.append("objects (id | kind | type | label):")
    for o in objs:
        lines.append(
            f"  {o['objectId']} | {o['kind']} | {o.get('nodeType')} | {o.get('label')}"
        )
    conns = layout.get("connectors", [])
    lines.append(f"connectors: {len(conns)}")
    for c in conns[:40]:
        lines.append(f"  {c['sourceObjectId']} -> {c['targetObjectId']} ({c.get('label')})")
    return "\n".join(lines)


def _review_stub(layout: dict, project_id: str, job_id: str, attempt: int) -> dict:
    settings = get_settings()
    # Deterministic heuristic: flag empty containers (no children) as LOW issues.
    by_parent: dict[str, int] = {}
    for o in layout.get("objects", []):
        pid = o.get("parentObjectId")
        if pid:
            by_parent[pid] = by_parent.get(pid, 0) + 1
    issues = []
    for o in layout.get("objects", []):
        if o.get("kind") == "CONTAINER" and by_parent.get(o["objectId"], 0) == 0:
            issues.append({
                "issueId": f"vr-{len(issues)+1}",
                "severity": "LOW",
                "rule": "EMPTY_GROUP",
                "objectIds": [o["objectId"]],
                "recommendedAction": {"type": "REGROUP"},
            })
    score = max(60, 100 - 5 * len(issues))
    decision = "PASS" if score >= settings.max_review_score_pass else "REVISE"
    return {
        "schemaVersion": "1.0",
        "projectId": project_id,
        "jobId": job_id,
        "attempt": attempt,
        "decision": decision,
        "score": score,
        "issues": issues,
        "summary": f"Stub review: {len(issues)} empty-group issue(s).",
    }


def _review_nova(
    preview_png: bytes, layout: dict, project_id: str, job_id: str, attempt: int
) -> dict:
    settings = get_settings()
    user_blocks = [
        {"text": "Review this AWS architecture slide.\n\n" + _layout_summary(layout)},
        {"image": {"format": "png", "source": {"bytes": preview_png}}},
        {"text": "Return ONLY the review-report JSON object."},
    ]
    resp = bedrock_runtime_client().converse(
        modelId=settings.review_model_id,
        system=[{"text": REVIEW_SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": user_blocks}],
        inferenceConfig={"maxTokens": 2048, "temperature": 0.2, "topP": 0.9},
    )
    text = resp["output"]["message"]["content"][0]["text"]
    report = _extract_json(text)
    report["projectId"] = project_id
    report["jobId"] = job_id
    report["attempt"] = attempt
    report.setdefault("schemaVersion", "1.0")
    report.setdefault("issues", [])
    # Coerce score to int if the model returned a float/string.
    if "score" in report:
        try:
            report["score"] = int(round(float(report["score"])))
        except (ValueError, TypeError):
            report["score"] = 70
    return report


def review(
    provider: str,
    preview_png: bytes,
    layout: dict,
    project_id: str,
    job_id: str,
    attempt: int,
) -> dict:
    if provider == "stub":
        return _review_stub(layout, project_id, job_id, attempt)
    if provider == "nova":
        return _review_nova(preview_png, layout, project_id, job_id, attempt)
    raise ReviewError(f"Unknown review provider: {provider}")
