"""Exercise real mini parsing with mocked API responses; no credentials required."""
import argparse
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

spec = importlib.util.spec_from_file_location("bench", Path(__file__).parents[1] / "oracle" / "bench.py")
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
HAS_MINI = importlib.util.find_spec("minisweagent") is not None


@unittest.skipUnless(HAS_MINI, "Install requirements-agent.txt for mini interface tests")
class MiniInterfaces(unittest.TestCase):
    def model(self, interface):
        from minisweagent.agents.default import AgentConfig
        from minisweagent.environments.docker import DockerEnvironmentConfig
        from minisweagent.models import get_model
        args = argparse.Namespace(interface=interface, model="small", steps=30, output_tokens=2048)
        cfg = bench.config(args)
        AgentConfig(**cfg["agent"])
        DockerEnvironmentConfig(image="placeholder", **cfg["environment"])
        return get_model(config=cfg["model"])

    def test_structured_action_and_usage(self):
        from litellm import ModelResponse
        model = self.model("tools")
        response = ModelResponse(choices=[{"finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function", "function": {
                    "name": "bash", "arguments": '{"command":"echo API_ACTION_OK"}'}}]}}],
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        with patch.object(model, "_query", return_value=response):
            message = model.query([{"role": "user", "content": "test"}])
        self.assertEqual(message["extra"]["actions"][0]["command"], "echo API_ACTION_OK")
        self.assertEqual(bench.metrics({"messages": [message]})["input_tokens_recorded"], 10)

    def test_text_action(self):
        from litellm import ModelResponse
        model = self.model("text")
        response = ModelResponse(choices=[{"message": {"role": "assistant",
            "content": "<mswea_bash_command>echo API_ACTION_OK</mswea_bash_command>"}}])
        with patch.object(model, "_query", return_value=response):
            message = model.query([{"role": "user", "content": "test"}])
        self.assertEqual(message["extra"]["actions"], [{"command": "echo API_ACTION_OK"}])

    def test_reasoning_only_not_executed_as_action(self):
        from litellm import ModelResponse
        from minisweagent.exceptions import FormatError
        model = self.model("text")
        response = ModelResponse(choices=[{"message": {"role": "assistant", "content": None,
            "reasoning_content": "<mswea_bash_command>echo API_ACTION_OK</mswea_bash_command>"}}])
        with patch.object(model, "_query", return_value=response):
            with self.assertRaises(FormatError):
                model.query([{"role": "user", "content": "test"}])


if __name__ == "__main__":
    unittest.main()
