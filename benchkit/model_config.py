"""Model config + token accounting for the framework.

Copied (not shared) from the oracle bench.py to sever benchkit's dependency on a
loose root script. The oracle retains its own copy as frozen reference; this is the
maintained one. Faithful to the verified behavior:
  - config(): mini model/agent/environment config (endpoint, temp 0, token caps);
    unknown university prices -> cost_tracking ignore, never a measured zero.
  - metrics(): sum per-response usage once; unknown usage stays null, not zero.
"""

from __future__ import annotations

import os

MODEL_NAMES = {"small": "small_model", "large": "Qwen/Qwen3.6-35B-A3B-FP8"}
BASE_URL = "https://xrlab-ie318-3.tmit.bme.hu/v1"


def config(args):
    import yaml
    from minisweagent.config import builtin_config_dir
    name = "swebench.yaml" if args.interface == "tools" else "swebench_xml.yaml"
    cfg = yaml.safe_load((builtin_config_dir / "benchmarks" / name).read_text())
    cfg["agent"].update(step_limit=args.steps, cost_limit=0)
    # Unknown university prices must not be reported as a measured zero cost.
    cfg["model"].update(
        model_name="openai/" + MODEL_NAMES[args.model],
        model_class="litellm" if args.interface == "tools" else "litellm_textbased",
        cost_tracking="ignore_errors",
        model_kwargs={
            "api_base": os.environ.get("BME_API_BASE", BASE_URL),
            "temperature": 0,
            "max_tokens": args.output_tokens,
            "timeout": 600,
            "num_retries": 0,
        },
    )
    cfg["environment"].update(
        environment_class="docker", timeout=120,
        env={**cfg["environment"].get("env", {}), "BASH_ENV": "/root/.bashrc"},
    )
    return cfg


def metrics(trajectory):
    """Sum per-response usage once. Unknown usage remains null, not zero."""
    calls = actions = format_errors = 0
    usages = []
    for msg in trajectory.get("messages", []):
        extra = msg.get("extra", {})
        response = extra.get("response")
        if isinstance(response, dict):
            calls += 1
            if isinstance(response.get("usage"), dict):
                usages.append(response["usage"])
        actions += len(extra.get("actions", []))
        format_errors += extra.get("interrupt_type") == "FormatError"

    def total(key):
        values = [u.get(key) for u in usages]
        return sum(values) if values and all(isinstance(v, int) for v in values) else None

    prompts = [u["prompt_tokens"] for u in usages if isinstance(u.get("prompt_tokens"), int)]
    return {
        "recorded_api_responses": calls,
        "responses_with_usage": len(usages),
        "usage_complete": calls > 0 and len(usages) == calls,
        "input_tokens_recorded": total("prompt_tokens"),
        "output_tokens_recorded": total("completion_tokens"),
        "max_input_tokens_recorded": max(prompts) if prompts else None,
        "tool_actions": actions,
        "format_errors": format_errors,
        "monetary_cost": None,
    }
