"""T176: the eval runner states its cost, skips an unchanged gate, and its fingerprint follows its inputs
(Constitution IV, research R29). No model calls: the turn itself is replaced."""

import argparse
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

RUNNER = Path(__file__).parents[1] / "evals" / "run_evals.py"


@pytest.fixture
def ev(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location("run_evals_under_test", RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    # A private copy of everything the fingerprint reads, so the test can change it.
    repo = tmp_path / "repo"
    agent = repo / "onboarding" / "agent"
    shutil.copytree(mod.AGENT / "src" / "onboarding_agent" / "prompts", agent / "src" / "onboarding_agent" / "prompts")
    shutil.copy(mod.AGENT / "src" / "onboarding_agent" / "loop.py", agent / "src" / "onboarding_agent" / "loop.py")
    shutil.copytree(mod.REPO / "onboarding" / "catalog" / "playbooks", repo / "onboarding" / "catalog" / "playbooks")
    cases = agent / "tests" / "evals" / "aws_saas_failures"
    shutil.copytree(mod.CASES, cases, ignore=shutil.ignore_patterns("*.png"))
    monkeypatch.setattr(mod, "AGENT", agent)
    monkeypatch.setattr(mod, "REPO", repo)
    monkeypatch.setattr(mod, "CASES", cases)
    monkeypatch.setattr(mod, "GATE_FILE", tmp_path / "evals-gate.json")
    calls: list[tuple] = []

    async def fake_once(case, mode):  # type: ignore[no-untyped-def]
        calls.append((case["id"], mode))
        return True, True, "**Side: AWS** …"

    async def fake_plan_once(case):  # type: ignore[no-untyped-def]
        calls.append(("plan", "text"))
        return True, True, "Still to do: …"

    async def no_model(*_, **__):  # type: ignore[no-untyped-def]
        raise AssertionError("the eval runner tests must never call the model")

    monkeypatch.setattr(mod, "once", fake_once)
    monkeypatch.setattr(mod, "plan_once", fake_plan_once)
    monkeypatch.setattr(mod.loop, "run_turn", no_model)
    mod.calls = calls
    return mod


def args(**kw):  # type: ignore[no-untyped-def]
    base = {"runs": 1, "cases": "", "mode": "both", "concurrency": 4, "verbose": False, "gate": False,
            "estimate": False, "force": False}
    return argparse.Namespace(**(base | kw))


def test_fingerprint_follows_its_inputs(ev, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    first = ev.fingerprint()
    assert ev.fingerprint() == first
    prompt = ev.AGENT / "src" / "onboarding_agent" / "prompts" / "en" / "system.md"
    prompt.write_text(prompt.read_text() + "\nOne more rule.")
    second = ev.fingerprint()
    assert second != first
    case_file = ev.CASES / "cases.yaml"
    case_file.write_text(case_file.read_text() + "\n# note\n")
    third = ev.fingerprint()
    assert third != second
    playbook = next((ev.REPO / "onboarding" / "catalog" / "playbooks" / "aws-saas").rglob("*.md"))
    playbook.write_text(playbook.read_text() + "\n")
    assert ev.fingerprint() != third
    # spec 002 T076: another connector type's playbook doesn't touch the AWS gate
    unchanged = ev.fingerprint()
    other = next((ev.REPO / "onboarding" / "catalog" / "playbooks" / "entra-id").rglob("*.md"))
    other.write_text(other.read_text() + "\n")
    assert ev.fingerprint() == unchanged
    assert ev.fingerprint("entra_failures") != unchanged
    before = ev.fingerprint()
    monkeypatch.setattr(ev, "MODEL_ID", "another-model")
    assert ev.fingerprint() != before


async def test_estimate_only_makes_no_call(ev, capsys) -> None:  # type: ignore[no-untyped-def]
    assert await ev.main(args(estimate=True, runs=3)) == 0
    out = capsys.readouterr().out
    assert "Claude Haiku calls" in out and "$" in out
    assert ev.calls == []


async def test_gate_runs_once_then_skips_until_forced(ev, capsys) -> None:  # type: ignore[no-untyped-def]
    assert await ev.main(args(gate=True, runs=2)) == 0
    first_run = len(ev.calls)
    assert first_run > 0 and json.loads(ev.GATE_FILE.read_text())["fingerprint"] == ev.fingerprint()
    assert await ev.main(args(gate=True, runs=2)) == 0
    assert len(ev.calls) == first_run  # skipped: nothing changed
    assert "not calling the model" in capsys.readouterr().out
    assert await ev.main(args(gate=True, runs=2, force=True)) == 0
    assert len(ev.calls) == 2 * first_run
