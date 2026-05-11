"""
DebugAgent — static + LLM-assisted ComfyUI workflow analysis.

Pipeline
--------
1. Fetch ``/api/object_info`` from the running ComfyUI server to get the
   schema of every installed node.
2. Run static validation (unknown nodes, dangling refs, wrong slot indices,
   missing required inputs).
3. Pass the workflow + issues to an LLM that writes a plain-English summary
   and optionally returns a corrected workflow JSON.

Public API
----------
``await debug_workflow(workflow, server_address, model, api_key)``
  → ``{"issues": [...], "summary": str, "fixed_workflow": dict | None}``
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Schema fetching
# ---------------------------------------------------------------------------


def _fetch_object_info(server_address: str) -> dict[str, Any]:
    """Return the full ``/api/object_info`` dict from ComfyUI."""
    url = f"http://{server_address}/api/object_info"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Static analysis
# ---------------------------------------------------------------------------


def _count_outputs(class_type: str, object_info: dict) -> int:
    schema = object_info.get(class_type, {})
    return len(schema.get("output", []))


def static_issues(workflow: dict, object_info: dict) -> list[dict]:
    """Return a list of issue dicts describing problems found in *workflow*.

    Each dict has:
      ``node_id``   — the node's string ID
      ``class_type`` — node class name
      ``kind``       — one of ``unknown_node | dangling_ref |
                        wrong_slot | missing_required``
      ``detail``     — human-readable description
    """
    issues: list[dict] = []
    output_slot_counts: dict[str, int] = {
        nid: _count_outputs(node.get("class_type", ""), object_info)
        for nid, node in workflow.items()
    }

    for nid, node in workflow.items():
        ct = node.get("class_type", "")
        if ct not in object_info:
            issues.append(
                {
                    "node_id": nid,
                    "class_type": ct,
                    "kind": "unknown_node",
                    "detail": (
                        f"Node class '{ct}' is not installed or not recognised "
                        "by this ComfyUI instance."
                    ),
                }
            )
            continue

        schema = object_info[ct]
        required = schema.get("input", {}).get("required", {})
        node_inputs = node.get("inputs") or {}

        for inp_name, _spec in required.items():
            if inp_name not in node_inputs:
                issues.append(
                    {
                        "node_id": nid,
                        "class_type": ct,
                        "kind": "missing_required",
                        "detail": (
                            f"Required input '{inp_name}' is neither connected "
                            "nor given a default value."
                        ),
                    }
                )
                continue

            val = node_inputs[inp_name]
            if isinstance(val, list) and len(val) == 2:
                src_id, src_slot = str(val[0]), int(val[1])
                if src_id not in workflow:
                    issues.append(
                        {
                            "node_id": nid,
                            "class_type": ct,
                            "kind": "dangling_ref",
                            "detail": (
                                f"Input '{inp_name}' references node {src_id} "
                                "which does not exist in this workflow."
                            ),
                        }
                    )
                elif src_slot >= output_slot_counts.get(src_id, 0):
                    src_ct = workflow[src_id].get("class_type", "?")
                    issues.append(
                        {
                            "node_id": nid,
                            "class_type": ct,
                            "kind": "wrong_slot",
                            "detail": (
                                f"Input '{inp_name}' uses output slot {src_slot} "
                                f"of '{src_ct}' (node {src_id}), but that node "
                                f"only has {output_slot_counts.get(src_id, 0)} "
                                "output(s)."
                            ),
                        }
                    )

    return issues


# ---------------------------------------------------------------------------
# LLM-assisted summary + fix
# ---------------------------------------------------------------------------


async def _llm_analyze(
    workflow: dict,
    issues: list[dict],
    model: str,
    api_key: str | None = None,
    api_base: str | None = None,
) -> tuple[str, dict | None]:
    """Ask the LLM for a plain-English summary and an optional corrected workflow.

    Returns ``(summary_text, fixed_workflow | None)``.
    """
    import litellm  # lazy import

    wf_str = json.dumps(workflow, indent=2)
    # Keep prompt under token limits
    if len(wf_str) > 8000:
        wf_str = wf_str[:8000] + "\n… (truncated)"

    if issues:
        issues_str = "\n".join(
            f"- [{i['node_id']}] {i['class_type']}: [{i['kind']}] {i['detail']}"
            for i in issues[:25]
        )
    else:
        issues_str = "(no static issues detected)"

    prompt = (
        "You are a ComfyUI workflow debugging expert.\n\n"
        "## Workflow (API format)\n```json\n" + wf_str + "\n```\n\n"
        "## Static analysis\n" + issues_str + "\n\n"
        "## Instructions\n"
        "1. Write a SUMMARY (3-6 sentences) explaining what is wrong and why.\n"
        "2. If there are fixable issues, output a corrected workflow as a JSON "
        "code block labelled ```json.  Only change broken parts. "
        "If nothing needs changing, write NO_FIX_NEEDED.\n\n"
        "Format your response exactly as:\n"
        "SUMMARY: <text>\n"
        "WORKFLOW: ```json\n<fixed workflow>\n``` or NO_FIX_NEEDED"
    )

    kwargs: dict = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if api_base:
        kwargs["api_base"] = api_base

    try:
        resp = await litellm.acompletion(**kwargs)
        text: str = resp.choices[0].message.content or ""
    except Exception as exc:
        return f"⚠️ LLM analysis failed: {exc}", None

    # Parse SUMMARY
    summary = ""
    if "SUMMARY:" in text:
        after = text.split("SUMMARY:", 1)[1]
        summary = (after.split("WORKFLOW:")[0] if "WORKFLOW:" in after else after).strip()

    # Parse fixed workflow
    fixed: dict | None = None
    if "```json" in text:
        try:
            raw = text.split("```json", 1)[1].split("```")[0].strip()
            if raw and raw != "NO_FIX_NEEDED":
                fixed = json.loads(raw)
        except Exception:
            pass

    return summary or text[:600], fixed


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def debug_workflow(
    workflow: dict,
    server_address: str,
    model: str,
    api_key: str | None = None,
    api_base: str | None = None,
) -> dict:
    """Analyse *workflow* and return a structured result.

    Returns
    -------
    ``{"issues": list, "summary": str, "fixed_workflow": dict | None}``
    """
    object_info = _fetch_object_info(server_address)
    issues = static_issues(workflow, object_info)
    summary, fixed = await _llm_analyze(workflow, issues, model, api_key, api_base)
    return {"issues": issues, "summary": summary, "fixed_workflow": fixed}
