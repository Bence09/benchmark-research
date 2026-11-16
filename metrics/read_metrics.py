"""Per-channel read attribution from a trajectory (E4-S1).

Pairs each assistant action with its following `tool` observation and attributes
the observation's delivered bytes/lines to a channel chosen by the first program
in the command's pipeline. Commands with an unknown leader go to `unattributed`
and are never silently folded into a channel or zeroed (tool_policy §10).

Trajectory shape (observed from real runs):
  assistant msg: extra.actions = [{command, tool_call_id}, ...]
  each action's output = the next `tool` message's content, in order.
"""

from __future__ import annotations

import shlex
from enum import Enum


class Channel(str, Enum):
    SEARCH = "search"       # grep, rg, find
    READ = "read"           # sed -n, nl, cat, head, tail, less
    INDEX = "index"         # index_query (the added tool)
    OTHER = "other"         # known-but-not-a-read command
    UNATTRIBUTED = "unattributed"  # unknown leader; volume not attributed to a read channel


_SEARCH = {"grep", "egrep", "fgrep", "rg", "ripgrep", "find", "ag"}
_READ = {"sed", "nl", "cat", "head", "tail", "less", "more", "awk_noop"}
_INDEX = {"index_query"}
# Known commands that are not reads of source (don't attribute their output as reads).
_OTHER_KNOWN = {"echo", "cd", "ls", "pwd", "wc", "true", "exit"}


def classify_command(command: str) -> Channel:
    """Classify by the first program in the pipeline. Unknown leader -> UNATTRIBUTED."""
    leader = _first_program(command)
    if leader is None:
        return Channel.UNATTRIBUTED
    if leader in _INDEX:
        return Channel.INDEX
    if leader in _SEARCH:
        return Channel.SEARCH
    if leader in _READ:
        return Channel.READ
    if leader in _OTHER_KNOWN:
        return Channel.OTHER
    return Channel.UNATTRIBUTED


def _first_program(command: str) -> str | None:
    """Leading program name of a (possibly piped) command line."""
    text = command.strip()
    if not text:
        return None
    # Take the first pipeline stage; ignore leading env assignments / redirects.
    first_stage = text.split("|", 1)[0].strip()
    try:
        tokens = shlex.split(first_stage)
    except ValueError:
        tokens = first_stage.split()
    for tok in tokens:
        if "=" in tok and not tok.startswith("-") and _looks_like_env(tok):
            continue  # VAR=value prefix
        # strip a path, keep the basename (e.g. /opt/index_query.py -> index_query.py)
        base = tok.rsplit("/", 1)[-1]
        if base.endswith(".py"):
            base = base[:-3]
        return base
    return None


def _looks_like_env(tok: str) -> bool:
    name = tok.split("=", 1)[0]
    return name.isidentifier()


def _observation_volume(content) -> tuple[int, int]:
    if not isinstance(content, str) or not content:
        return 0, 0
    return len(content.encode("utf-8")), len(content.splitlines())


def read_metrics(trajectory: dict) -> dict:
    """Attribute delivered read volume per channel. Merge into metrics.json."""
    messages = trajectory.get("messages", [])
    channels = {c.value: {"calls": 0, "bytes": 0, "lines": 0} for c in Channel}

    # Flatten: ordered list of (command) actions, and ordered list of tool observations.
    pending_actions: list[str] = []
    obs_iter_index = 0
    # We walk messages; when we hit an assistant with actions we queue the commands,
    # and consume the subsequent `tool` messages as their observations in order.
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        extra = msg.get("extra", {}) or {}
        actions = extra.get("actions", []) or []
        if actions:
            commands = [a.get("command", "") for a in actions]
            # consume the next len(commands) tool messages as observations
            obs = []
            j = i + 1
            while j < n and len(obs) < len(commands):
                if messages[j].get("role") == "tool":
                    obs.append(messages[j].get("content"))
                    j += 1
                elif messages[j].get("role") == "tool" or messages[j].get("role") == "user":
                    j += 1
                else:
                    break
            for k, cmd in enumerate(commands):
                ch = classify_command(cmd)
                entry = channels[ch.value]
                entry["calls"] += 1
                if k < len(obs):
                    b, ln = _observation_volume(obs[k])
                    entry["bytes"] += b
                    entry["lines"] += ln
            i = j
            continue
        i += 1

    read_channels = (Channel.SEARCH.value, Channel.READ.value, Channel.INDEX.value)
    lines_total = sum(channels[c]["lines"] for c in read_channels)
    bytes_total = sum(channels[c]["bytes"] for c in read_channels)
    unattributed_calls = channels[Channel.UNATTRIBUTED.value]["calls"]

    return {
        "channels": channels,
        "lines_read_total": lines_total,
        "bytes_read_total": bytes_total,
        # Honesty: calls whose read volume we could not attribute to a read channel.
        "unattributed_calls": unattributed_calls,
        "unattributed_bytes": channels[Channel.UNATTRIBUTED.value]["bytes"],
    }
