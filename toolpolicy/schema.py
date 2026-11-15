"""Load and validate a tool-policy YAML file (E1-S1).

Grammar (see docs/BACKLOG.md E1, D1/D3/D4/D6):

    version: 1
    conditions:
      <name>:
        description: <str, optional>
        extends: <parent condition name, optional>   # D2 additivity
        tools:
          <tool>:
            directive: allow | add | deny | remove    # D3
            payload: {kind: <str>, ...}               # required for 'add' (D4, opaque)
            limits:  {<str>: <num>, ...}              # optional
            result_shape: <str>                       # optional

This module only parses and structurally validates. It does NOT apply `extends`
or derive enforcement status; that is the resolver's job (E1-S2).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DIRECTIVES = {"allow", "add", "deny", "remove"}

# Directives that are only ever best-effort under the first-cut (soft) enforcement.
# `remove` is in the grammar but parked for strict enforcement (E5-B).
BEST_EFFORT_ONLY = {"remove"}


class PolicyError(ValueError):
    """Raised for any malformed or contradictory policy (R10: never silently weaken)."""


def load_policy(source: str | Path | dict) -> dict:
    """Parse and structurally validate a policy. Returns the raw (unresolved) dict.

    `source` may be a path, a YAML string, or an already-parsed dict (for tests).
    """
    data = _read(source)
    _validate_top_level(data)
    for name, cond in data["conditions"].items():
        _validate_condition(name, cond, data["conditions"])
    return data


def _read(source: str | Path | dict) -> dict:
    if isinstance(source, dict):
        return source
    import yaml

    text = Path(source).read_text() if _looks_like_path(source) else str(source)
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"invalid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PolicyError("policy must be a mapping at the top level")
    return parsed


def _looks_like_path(source: str | Path) -> bool:
    if isinstance(source, Path):
        return True
    # A YAML document contains a newline or a colon; a path usually does not.
    return "\n" not in source and source.strip().endswith((".yaml", ".yml"))


def _validate_top_level(data: dict) -> None:
    if data.get("version") != 1:
        raise PolicyError(f"unsupported policy version: {data.get('version')!r} (expected 1)")
    conditions = data.get("conditions")
    if not isinstance(conditions, dict) or not conditions:
        raise PolicyError("policy must define a non-empty 'conditions' mapping")


def _validate_condition(name: str, cond: Any, all_conditions: dict) -> None:
    if not isinstance(cond, dict):
        raise PolicyError(f"condition {name!r} must be a mapping")

    parent = cond.get("extends")
    if parent is not None and parent not in all_conditions:
        raise PolicyError(f"condition {name!r} extends unknown condition {parent!r}")
    if parent == name:
        raise PolicyError(f"condition {name!r} cannot extend itself")

    tools = cond.get("tools", {})
    if not isinstance(tools, dict):
        raise PolicyError(f"condition {name!r}: 'tools' must be a mapping")

    for tool, spec in tools.items():
        _validate_tool(name, tool, spec)


def _validate_tool(cond_name: str, tool: str, spec: Any) -> None:
    if not isinstance(spec, dict):
        raise PolicyError(f"{cond_name}.{tool}: tool spec must be a mapping")

    directive = spec.get("directive")
    if directive not in DIRECTIVES:
        raise PolicyError(
            f"{cond_name}.{tool}: unknown or missing directive {directive!r} "
            f"(expected one of {sorted(DIRECTIVES)})"
        )

    # D4: 'add' must carry an opaque payload with a 'kind'; we never interpret kind.
    if directive == "add":
        payload = spec.get("payload")
        if not isinstance(payload, dict) or not payload.get("kind"):
            raise PolicyError(
                f"{cond_name}.{tool}: directive 'add' requires a 'payload' mapping "
                f"with a non-empty 'kind'"
            )

    limits = spec.get("limits")
    if limits is not None and not isinstance(limits, dict):
        raise PolicyError(f"{cond_name}.{tool}: 'limits' must be a mapping")
