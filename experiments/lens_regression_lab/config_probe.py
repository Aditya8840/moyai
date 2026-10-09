"""Run actual config selection code and show what the eval driver overrides.

This is a deterministic configuration experiment, not an agent-quality score.
It makes no model, Lens, GitHub or production requests and reads no credentials.
"""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
from types import ModuleType


ORIGINAL_REVISION = 'ffcc83e054d45242a8a1a6465d1f775761ad6452'


def snapshot(root, revision, path):
    return subprocess.check_output(['git', 'show', f'{revision}:{path}'], cwd=root, text=True)


def model_settings(root, revision):
    path = root / "app/config.py"
    source = snapshot(root, revision, 'app/config.py')
    tree = ast.parse(source)
    settings = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Settings")
    names = {"allowed_models", "default_harness", "resolve_model", "model_choices"}
    methods = [node for node in settings.body if isinstance(node, ast.FunctionDef) and node.name in names]
    catalog = next(node for node in tree.body if isinstance(node, ast.AnnAssign)
                   and isinstance(node.target, ast.Name) and node.target.id == "MODEL_CATALOG")
    namespace = {"MODEL_CATALOG": ast.literal_eval(catalog.value)}
    module = ast.Module(body=[ast.ClassDef(name="SourceSettings", bases=[], keywords=[], body=methods,
                                         decorator_list=[])], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), namespace)
    return namespace["SourceSettings"], {
        "path": "app/config.py",
        "sha256": hashlib.sha256(source.encode()).hexdigest(),
        "methods_executed": sorted(names),
        "method_lines": {node.name: node.lineno for node in methods},
    }


def production_selection(settings_type, model, harness=None):
    settings = settings_type()
    settings.agent_model = model
    settings.agent_harness = harness or "claude-agent-sdk"
    settings.model_fields_set = {"agent_model"} | ({"agent_harness"} if harness else set())
    return {"model": settings.resolve_model(), "harness": settings.default_harness()}


def eval_agent_type(root, revision):
    module = ModuleType('lens_config_probe_agent')
    module.__file__ = str(root / 'evals/agent.py')
    sys.modules[module.__name__] = module
    exec(compile(snapshot(root, revision, 'evals/agent.py'), module.__file__, 'exec'), module.__dict__)
    return module.MoyaiAgent


def dependency_source_probe(root, revision):
    source = snapshot(root, revision, '.github/workflows/lens-evals.yml')
    assert '"$GITHUB_WORKSPACE/.venv/bin/python" -m pytest evals/test_moyai.py' in source
    assert "run: uv sync --frozen --group dev --group eval" in source
    assert "cp evals/agent.py evals/agent_worker.py evals/test_moyai.py evals/coding_cases.json" in source
    lock = tomllib.loads(snapshot(root, revision, 'uv.lock'))
    packages = {row["name"]: row["version"] for row in lock["package"]
                if row["name"] in {"openai-codex", "claude-agent-sdk", "openai-agents", "lens-evals"}}
    with tempfile.TemporaryDirectory(prefix="lens-dependency-probe-") as directory:
        base = Path(directory) / "base"
        head = Path(directory) / "head"
        base.mkdir()
        head.mkdir()
        # Stand-in import artifacts prove Python environment selection. These do
        # not claim to execute either vendor SDK or measure its quality.
        (base / "sdk_revision_probe.py").write_text("REVISION = 'base-sdk-version'\n")
        (head / "sdk_revision_probe.py").write_text("REVISION = 'candidate-sdk-version'\n")
        code = "import sdk_revision_probe; print(sdk_revision_probe.REVISION)"
        def selected(cwd):
            return subprocess.check_output([sys.executable, "-c", code], cwd=cwd,
                                           env={"PYTHONPATH": str(head)}, text=True).strip()
        # Keep the cwd distinct from the fixture import path, matching CI where
        # installed dependencies live in HEAD's site-packages for both runs.
        base_work = base / "checkout"
        head_work = head / "checkout"
        base_work.mkdir()
        head_work.mkdir()
        before = selected(base_work)
        after = selected(head_work)
    return {"kind": "import-path demonstration grounded in workflow configuration",
            "base_declared_artifact": "base-sdk-version", "head_declared_artifact": "candidate-sdk-version",
            "base_imported_artifact": before, "head_imported_artifact": after,
            "versions_are_distinct_in_this_experiment": False,
            "actual_head_locked_versions": packages,
            "workflow_dependencies_source": "HEAD .venv for both base and head",
            "workflow_driver_source": "HEAD eval driver and fixtures copied into base checkout"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval-model", default="openai/gpt-6.1-sol")
    parser.add_argument('--revision', default=ORIGINAL_REVISION,
                        help='Audited source snapshot, pinned before the regression-lab fixes')
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    version = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    settings_type, source = model_settings(root, arguments.revision)
    agent_type = eval_agent_type(root, arguments.revision)
    workflow = snapshot(root, arguments.revision, '.github/workflows/lens-evals.yml')
    harness_match = re.search(r"^      MOYAI_EVAL_HARNESS: ([a-z-]+)$", workflow, re.MULTILINE)
    if harness_match is None:
        raise ValueError("Workflow harness selection changed; update this probe to evaluate the actual workflow")
    # Non-secret inert values satisfy shape validation. from_env never contacts
    # these endpoints or runs the agent, so no usable credential is needed.
    environment = {"LITELLM_API_BASE": "https://inert.example", "LITELLM_API_KEY": "inert-placeholder",
                   "AGENT_MODEL": arguments.eval_model, "LITELLM_TRACE_ENDPOINT": "https://inert.example/v1/traces",
                   "LITELLM_TRACE_API_KEY": "inert-placeholder", "LENS_VERSION": version,
                   "MOYAI_EVAL_HARNESS": harness_match.group(1)}
    scenarios = (
        ("baseline_openai", arguments.eval_model, None),
        ("model_and_sdk_switch", "anthropic/claude-opus-5-5", None),
        ("model_change_same_sdk", "openai/gpt-6-astra", None),
        ("explicit_harness_change", arguments.eval_model, "tool-loop"),
    )
    results = []
    for name, model, harness in scenarios:
        production = production_selection(settings_type, model, harness)
        agent = agent_type.from_env(workspace=Path("/tmp/unexecuted-lens-probe"), environ=environment)
        evaluated = {"model": agent.model, "harness": agent.harness}
        results.append({"scenario": name, "configured_production": production, "effective_eval": evaluated,
                        "config_matches": production == evaluated})
    assert results[0]["config_matches"]
    assert all(result["effective_eval"] == results[0]["effective_eval"] for result in results)
    assert all(not result["config_matches"] for result in results[1:])
    report = {"experiment": "Moyai eval configuration coverage", "source_revision": arguments.revision,
              'checkout_revision': version,
              "scope": "Actual source methods and from_env executed; no model quality or live eval was measured",
              "model_setting": arguments.eval_model, "source": source, "scenarios": results,
              "mutated_production_configurations": 3, "mutations_reflected_in_eval_configuration": 0,
              "dependency_probe": dependency_source_probe(root, arguments.revision)}
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
