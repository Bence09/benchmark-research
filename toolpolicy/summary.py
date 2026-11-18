"""Render an inspectable pre-run summary of a resolved policy.

Just formatting, turns the resolver's effective-policy dict into readable text before the model runs.
No side effects.
"""

from __future__ import annotations


def render_summary(effective: dict) -> str:
    lines: list[str] = []
    cond = effective.get("condition")
    if cond is None or effective.get("default_environment"):
        lines.append(
            "Tool policy: <none selected> — default environment, no intervention (R11)."
        )
        return "\n".join(lines)

    lines.append(f"Tool policy condition: {cond}")
    lines.append(f"  declared sha256: {effective.get('declared_sha256')}")
    lines.append(f"  resolved sha256: {effective.get('resolved_sha256')}")
    lines.append("  tools:")
    for t in effective.get("tools", []):
        row = f"    - {t['name']:<12} {t['directive']:<7} status={t['status']:<8} enforcement={t['enforcement']}"
        extras = []
        if "payload_kind" in t:
            extras.append(f"payload={t['payload_kind']}")
        if t.get("index_ref_sha256"):
            extras.append(f"index_ref={t['index_ref_sha256'][:12]}…")
        if "result_shape" in t:
            extras.append(f"shape={t['result_shape']}")
        if "limits" in t:
            extras.append(f"limits={t['limits']}")
        if extras:
            row += "  (" + ", ".join(extras) + ")"
        lines.append(row)

    # show any best-effort restriction so a reader does not
    # over-claim enforcement level
    soft = [
        t["name"]
        for t in effective.get("tools", [])
        if t["enforcement"] == "best-effort" and t["status"] in {"removed", "denied"}
    ]
    if soft:
        lines.append(
            f"  note: best-effort restriction only on {soft} — recorded, not strictly enforced."
        )
    return "\n".join(lines)
