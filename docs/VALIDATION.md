# Validation record — 2026-11-02

Passed:

- Python compilation and CLI help.
- Bash syntax check of setup.sh.
- Five offline orchestration/accounting tests.
- Three tests using installed mini-SWE-agent 2.4.6 and its real model parsers,
  with mocked API responses: structured action parsing and usage retention,
  text-action parsing, and rejection of reasoning-only text as an action.
- Agent/environment configuration validation is included in those interface tests.

Full result: 8 tests passed, no skips in the installed test environment.
Tests use LiteLLM's bundled cost map to avoid a network fetch during import.

Not performed:

- University endpoint authentication or actual model action compatibility.
- Docker image startup, repository tests or official SWE-bench grading.
- Installation/execution on the user's Windows/WSL machine.
- SWE-Explore wrapper integration or scoring.

The test environment used the published mini-SWE-agent 2.4.6 package; the starter's
requirements pin the inspected upstream source commit reporting the same version.
Run the included tests after installing the pinned source on your machine.
The evaluator invocation test checks command construction using a mock, not grading.

This validates the starter's local interfaces and accounting, not benchmark outcomes.
