"""Resolve a declared policy condition into an effective policy (E1-S2).

Applies `extends` additivity (D2), derives enforcement status rather than trusting
the author (D5), validates required capabilities (D6/R4), preserves the untouched
default when no condition is selected (R11), and hashes both the declared condition
and the resolved block (R9).

Enforcement model (first cut, soft — see E0 decision 4):
  - allow  -> status present,  enforcement n/a        (task-essential, expected)
  - add    -> status added,    enforcement best-effort (prompt+logging; bash can bypass)
  - deny   -> status denied,   enforcement enforced    (only for capabilities a real
               mechanism backs, e.g. network via container netns=none)
  - remove -> status removed,  enforcement best-effort (parked for strict; E5-B)
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .schema import load_policy

# Capabilities for which a real enforcement mechanism exists in the first cut.
# `deny` on these is genuinely enforced; `deny` elsewhere degrades to best-effort.
HARD_ENFORCEABLE_DENY = {"network"}

_ENFORCEMENT = {
    "allow": ("present", "n/a"),
    "add": ("added", "best-effort"),
    "remove": ("removed", "best-effort"),
    # deny handled specially (depends on the capability)
}


class ResolutionError(ValueError):
    """Raised when a resolved policy is unusable (e.g. missing required capability)."""


def resolve(
    policy: dict | str,
    condition: str | None,
    required_capabilities: list[str] | None = None,
) -> dict:
    """Resolve one named condition into an effective policy.

    `condition=None` means no intervention selected: returns the untouched-default
    marker (R11) rather than inventing tools.
    """
    if condition is None:
        return {
            "condition": None,
            "default_environment": True,
            "tools": [],
            "declared_sha256": None,
            "resolved_sha256": None,
        }

    data = policy if isinstance(policy, dict) and "conditions" in policy else load_policy(policy)
    conditions = data["conditions"]
    if condition not in conditions:
        raise ResolutionError(f"unknown condition {condition!r}")

    merged_tools = _merge_with_parents(condition, conditions)
    effective = [_effective_tool(name, spec) for name, spec in merged_tools.items()]

    _check_required(effective, required_capabilities or [])

    declared = _canonical(conditions[condition])
    resolved_block = {
        "condition": condition,
        "default_environment": False,
        "tools": effective,
    }
    return {
        **resolved_block,
        "declared_sha256": _sha256(declared),
        "resolved_sha256": _sha256(_canonical(resolved_block)),
    }


def _merge_with_parents(name: str, conditions: dict) -> dict:
    """Apply `extends` (D2): parent tools first, child tools override/add on top.

    Child directives win. A child that both inherits and redeclares a tool replaces
    the parent entry. A child that `remove`s what it (or a parent) `add`s is a
    contradiction and rejected (D6).
    """
    chain: list[str] = []
    cur: str | None = name
    seen = set()
    while cur is not None:
        if cur in seen:
            raise ResolutionError(f"cyclic 'extends' involving {cur!r}")
        seen.add(cur)
        chain.append(cur)
        cur = conditions[cur].get("extends")

    merged: dict[str, Any] = {}
    for cond_name in reversed(chain):  # root parent first
        for tool, spec in conditions[cond_name].get("tools", {}).items():
            merged[tool] = spec

    # D6 contradiction check across the fully merged view.
    for tool, spec in merged.items():
        if spec.get("directive") == "remove":
            # Was it added anywhere up the chain and not finally overridden? The
            # final merged directive is 'remove', so any prior 'add' is contradicted.
            for cond_name in chain:
                prior = conditions[cond_name].get("tools", {}).get(tool)
                if prior and prior.get("directive") == "add":
                    raise ResolutionError(
                        f"condition {name!r}: tool {tool!r} is both 'add' and 'remove' "
                        f"across the extends chain (contradiction)"
                    )
    return merged


def _effective_tool(name: str, spec: dict) -> dict:
    directive = spec["directive"]
    if directive == "deny":
        enforcement = "enforced" if name in HARD_ENFORCEABLE_DENY else "best-effort"
        status = "denied"
    else:
        status, enforcement = _ENFORCEMENT[directive]

    entry = {"name": name, "directive": directive, "status": status, "enforcement": enforcement}
    if "payload" in spec:
        payload = spec["payload"]
        entry["payload_kind"] = payload.get("kind")
        # index_ref is resolved/hashed at prepare time (E2); null until then.
        entry["index_ref_sha256"] = payload.get("index_ref")
    if "limits" in spec:
        entry["limits"] = spec["limits"]
    if "result_shape" in spec:
        entry["result_shape"] = spec["result_shape"]
    return entry


def _check_required(effective: list[dict], required: list[str]) -> None:
    """R4/D6: every required capability must resolve to something usable, before run."""
    present = {t["name"] for t in effective if t["status"] in {"present", "added"}}
    missing = [cap for cap in required if cap not in present]
    if missing:
        raise ResolutionError(
            f"policy does not provide required capabilities: {missing} "
            f"(present: {sorted(present)})"
        )


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
