"""Tool-policy layer (E1).

Pure, offline resolution of a declared YAML tool policy into an effective,
inspectable, hashable policy. Knows nothing about mini-SWE-agent, Docker or the
API. Wiring the resolved policy into an agent environment and installing/injecting
tools belongs to E2/E3, not here.

See docs/BACKLOG.md, Epic 1, for the settled schema design (D1-D6).
"""

from .schema import PolicyError, load_policy
from .resolver import resolve
from .summary import render_summary

__all__ = ["PolicyError", "load_policy", "resolve", "render_summary"]
