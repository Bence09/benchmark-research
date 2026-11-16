"""Failure classification (E4-S1, tool_policy §6).

Separates the four categories so summaries never conflate "crashed" with
"answered badly":
  - infra:           container/transport/setup failures outside our control
  - policy_setup:    missing required capability, index build failure, bad config
  - output_limit:    model hit step / token / length budget
  - valid_but_wrong: a valid submission that graded poorly (not a failure of the
                     run, a result)
  - ok:              resolved / scored as intended
"""

from __future__ import annotations

from enum import Enum


class FailureCategory(str, Enum):
    OK = "ok"
    INFRA = "infra"
    POLICY_SETUP = "policy_setup"
    OUTPUT_LIMIT = "output_limit"
    VALID_BUT_WRONG = "valid_but_wrong"


_INFRA = {"SetupError", "ConnectionError", "Timeout", "APIError", "DockerError"}
_LIMIT = {"LimitsExceeded", "length", "StepLimitExceeded", "ContextWindowExceeded"}


def classify_failure(exit_status, *, has_valid_submission=False,
                     resolved=None, policy_error=False) -> FailureCategory:
    """Map a run's outcome to a failure category.

    `resolved`/`has_valid_submission` come from the benchmark evaluator; a valid
    but low-scoring submission is VALID_BUT_WRONG, not a failure of the harness.
    """
    if policy_error:
        return FailureCategory.POLICY_SETUP
    status = (exit_status or "").strip()

    if status in _INFRA:
        return FailureCategory.INFRA
    if status in _LIMIT:
        return FailureCategory.OUTPUT_LIMIT

    if status in ("Submitted", "Completed", "Finished") or has_valid_submission:
        if resolved is True:
            return FailureCategory.OK
        if resolved is False:
            return FailureCategory.VALID_BUT_WRONG
        # No grading verdict yet: a valid submission is OK at the run level.
        return FailureCategory.OK if has_valid_submission else FailureCategory.OUTPUT_LIMIT

    # Unknown / empty status with no submission: treat as infra (safest non-result).
    return FailureCategory.INFRA
