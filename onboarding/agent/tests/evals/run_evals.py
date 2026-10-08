"""Diagnosis evals for the AWS SaaS playbook (SC-005): does the agent's first reply name the cause and the side?

  uv run --project onboarding/agent --group evals python onboarding/agent/tests/evals/run_evals.py [--runs 3]
      [--gate] [--estimate] [--force] [--cases F1,F4] [--mode text|screenshot|both] [--concurrency 4]

Cost (Constitution IV, research R29): every run calls Claude Haiku on Bedrock, so it prints its estimate first and the
actual calls, tokens and cost after. The default is 3 runs per case (a quick read while working). `--gate` is the
SC-005 gate (10 runs, pass at 9/10); a passing gate records a fingerprint of everything it depends on (prompts,
playbooks, the tool loop, the cases, the model id) in .run/evals-gate.json, and a later `--gate` with the same
fingerprint prints that result and stops without calling the model, unless `--force`. `--estimate` only prints the
estimate.

Each case in aws_saas_failures/cases.yaml is a participant pasting an error (text) or uploading it (a PNG rendered
from the same text). The turn runs through the real loop and real Claude Haiku on Bedrock; SailPoint tools are
replaced by a fake that returns the same error, so nothing leaves the machine except the Bedrock call. A case passes
when at least 90% of its runs (9/10 by default) name the expected side and match one of its cause patterns.
Exit code 1 when any case misses the bar.
"""

import argparse
import asyncio
import base64
import hashlib
import io
import json
import os
import re
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import yaml

from onboarding_agent import loop, playbooks
from onboarding_agent.tools.session import SessionTools

HERE = Path(__file__).parent
CASES = HERE / "aws_saas_failures"
AGENT = HERE.parents[1]
REPO = AGENT.parents[1]
GATE_FILE = REPO / ".run" / "evals-gate.json"
GATE_RUNS = 10
MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
# Claude Haiku 4.5 list prices on Bedrock, USD per million tokens (kept next to the model id they belong to).
PRICE = {"input_tokens": 1.00, "cache_write_tokens": 1.25, "cache_read_tokens": 0.10, "output_tokens": 5.00}
# Measured on 2026-10-07 (3,959 calls): about 5 model calls per eval turn, 140 output tokens per call; with caching
# (research R27) about USD 0.004 per call.
CALLS_PER_TURN = 5
USD_PER_CALL = 0.004
EXTERNAL_ID = "5c0d9a3e-7b14-4f2a-9e61-2d8a4c7b1f90"
PASS_RATE = 0.9
SIDE = {
    "application": re.compile(r"\bside\b\W{0,4}AWS\b|\bAWS\b[^.\n]{0,40}\bside\b", re.I),
    "sailpoint": re.compile(r"\bside\b\W{0,4}SailPoint\b|\bSailPoint\b[^.\n]{0,40}\bside\b", re.I),
}


def session() -> dict:
    return {
        "id": "eval", "connector_type": "aws-saas",
        "tenant": {"name": "acme-demo", "api_host": "acme-demo.api.identitynow-demo.com",
                   "credential_provider": "onboarding-isc-acme-demo", "external_id": EXTERNAL_ID},
        "details": {"source_name": "AWS - Acme Org", "source_owner": "w.rakkiatngam",
                    "management_account_id": "111122223333", "accounts": ["111122223333", "444455556666"],
                    "region": "ap-southeast-1", "role_name": "SailPointISCRole-acme-demo",
                    "bedrock_regions": [], "agentcore_regions": ["ap-southeast-1"]},
        "steps": {"application_ready": "in_progress", "source_created": "passed", "configured": "passed",
                  "connection_check": "failed"},
        "source": {"id": "0" * 31 + "1", "name": "AWS - Acme Org"},
    }


class FailingIsc:
    """SailPoint tools named in the case's `fails` (all of them by default) report its error, so a rerun reproduces
    the failure instead of hiding it; the others succeed."""

    def __init__(self, case: dict):
        self.error = case["error"]
        self.fails = set(case.get("fails") or [])

    def __getattr__(self, name: str) -> Any:
        async def call(**_: Any) -> dict:
            if self.fails and name not in self.fails:
                return {"status": "passed"}
            return {"status": "failed", "error": self.error}

        return call


def screenshot(case: dict) -> bytes:
    path = CASES / f"{case['id']}.png"
    if path.exists():
        return path.read_bytes()
    from PIL import Image, ImageDraw, ImageFont

    lines = ["SailPoint Identity Security Cloud" if case["speaker"] == "iam_engineer" else "AWS CloudShell", ""]
    lines += textwrap.wrap(case["error"], 88)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 18)
    except OSError:
        font = ImageFont.load_default(size=18)
    img = Image.new("RGB", (1100, 60 + 28 * len(lines)), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 1100, 44), fill="#1f2937")
    draw.text((16, 12), lines[0], fill="white", font=font)
    for i, line in enumerate(lines[2:]):
        draw.text((16, 64 + 28 * i), line, fill="#b91c1c", font=font)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    path.write_bytes(buf.getvalue())
    return buf.getvalue()


def payload(case: dict, mode: str) -> dict:
    who = case["speaker"]
    name = {"iam_engineer": "Wanchai R.", "application_owner": "Ploy S."}[who]
    if mode == "text":
        msg = f"We got this error:\n\n{case['error']}"
        images = []
    else:
        msg = "We got this error, see the screenshot."
        images = [{"media_type": "image/png", "data_b64": base64.b64encode(screenshot(case)).decode()}]
    return {
        "turn_id": f"eval-{case['id']}-{mode}",
        "ordered_by": {"user_id": who, "display_name": name, "role": who},
        "session": session(),
        "history": [
            {"speaker": "iam_engineer", "text": "Run the connection check."},
            {"speaker": "agent", "text": "Running the connection check now."},
        ],
        "message": {"speaker": who, "thread": who, "text": msg},
        "images": images,
        "waiting_on": None, "check_order": None, "suggestion_defaults": {},
    }


def fingerprint() -> str:
    """Everything the gate's result depends on (Constitution IV: no repeat without a change)."""
    h = hashlib.sha256(MODEL_ID.encode())
    roots = [AGENT / "src" / "onboarding_agent" / "prompts", REPO / "onboarding" / "catalog" / "playbooks", CASES]
    files = sorted(f for r in roots for f in r.rglob("*") if f.is_file() and f.suffix != ".png")
    files.append(AGENT / "src" / "onboarding_agent" / "loop.py")
    for f in files:
        h.update(str(f.relative_to(REPO)).encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def estimate(n_turns: int) -> tuple[int, float]:
    calls = n_turns * CALLS_PER_TURN
    return calls, calls * USD_PER_CALL


def cost(usage: dict) -> float:
    return sum(usage.get(k, 0) * p for k, p in PRICE.items()) / 1_000_000


async def once(case: dict, mode: str) -> tuple[bool, bool, str]:
    events: list[dict] = []

    async def emit(e: dict) -> None:
        events.append(e)

    p = payload(case, mode)
    pb = playbooks.for_session(p["session"])
    await loop.run_turn(p, pb, FailingIsc(case), SessionTools(emit, p["ordered_by"]["role"]), emit)  # type: ignore[arg-type]
    reply = next((e["text"] for e in events if e["type"] == "final"), "")
    for e in events:
        if e["type"] == "usage":
            for k, v in e.items():
                if k != "type":
                    USAGE[k] = USAGE.get(k, 0) + v
    side_ok = bool(SIDE[case["side"]].search(reply))
    cause_ok = any(re.search(c, reply, re.I) for c in case["cause"])
    return side_ok, cause_ok, reply


USAGE: dict[str, int] = {}


async def plan_once(case: dict) -> tuple[bool, bool, str]:
    """T150: the answer to "What is left to do?" follows the plan (FR-008b)."""
    events: list[dict] = []

    async def emit(e: dict) -> None:
        events.append(e)

    p = payload({"id": "plan", "speaker": case["speaker"], "error": ""}, "text")
    p["message"]["text"] = case["message"]
    p["session"]["plan"] = case["plan"]
    pb = playbooks.for_session(p["session"])
    await loop.run_turn(p, pb, FailingIsc({"error": "not used", "fails": ["none"]}), SessionTools(emit, case["speaker"]),
                        emit)  # type: ignore[arg-type]
    for e in events:
        if e["type"] == "usage":
            for k, v in e.items():
                if k != "type":
                    USAGE[k] = USAGE.get(k, 0) + v
    reply = next((e["text"] for e in events if e["type"] == "final"), "")
    positions = [m.start() if (m := re.search(re.escape(w), reply, re.I)) else -1 for w in case["left_in_order"]]
    in_order = all(x >= 0 for x in positions) and positions == sorted(positions)
    not_listed = not any(re.search(re.escape(w), reply, re.I) for w in case.get("not_left", []))
    return in_order, not_listed, reply


async def main(args: argparse.Namespace) -> int:
    cases = yaml.safe_load((CASES / "cases.yaml").read_text())
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    modes = ["text", "screenshot"] if args.mode == "both" else [args.mode]
    full_gate = args.gate and not args.cases and args.mode == "both"
    print_ = fingerprint() if full_gate else ""
    if full_gate and not args.force and GATE_FILE.exists():
        last = json.loads(GATE_FILE.read_text())
        if last.get("fingerprint") == print_:
            print(f"Gate unchanged since its last pass ({last['at']}: {last['result']}); not calling the model. "
                  "Use --force to run it anyway.")
            return 0
    calls, usd = estimate((len(cases) * len(modes) + (0 if args.cases else 1)) * args.runs)
    print(f"Estimate: {len(cases)} cases × {len(modes)} modes × {args.runs} runs → about {calls} Claude Haiku calls "
          f"on Bedrock, about ${usd:.2f}{' (forced)' if args.force else ''}.")
    if args.estimate:
        return 0
    started = time.monotonic()
    sem = asyncio.Semaphore(args.concurrency)

    async def guarded(case: dict, mode: str) -> tuple[bool, bool, str]:
        async with sem:
            try:
                return await once(case, mode)
            except Exception as exc:  # noqa: BLE001 — a failed run counts as a miss
                return False, False, f"(error: {type(exc).__name__}: {exc})"

    failed = 0
    print(f"{'case':<6}{'mode':<12}{'side':>6}{'cause':>7}{'both':>6}  result")
    for case in cases:
        for mode in modes:
            runs = await asyncio.gather(*(guarded(case, mode) for _ in range(args.runs)))
            side = sum(r[0] for r in runs)
            cause = sum(r[1] for r in runs)
            both = sum(r[0] and r[1] for r in runs)
            ok = both >= PASS_RATE * args.runs
            failed += not ok
            print(f"{case['id']:<6}{mode:<12}{side:>6}{cause:>7}{both:>6}  {'PASS' if ok else 'FAIL'}")
            if not ok and args.verbose:
                miss = next(r[2] for r in runs if not (r[0] and r[1]))
                print(textwrap.indent(miss[:700], "      | "))
    if not args.cases or "plan" in args.cases.split(","):
        plan_case = yaml.safe_load((CASES / "plan_left.yaml").read_text())

        async def guarded_plan() -> tuple[bool, bool, str]:
            async with sem:
                try:
                    return await plan_once(plan_case)
                except Exception as exc:  # noqa: BLE001
                    return False, False, f"(error: {type(exc).__name__}: {exc})"

        runs = await asyncio.gather(*(guarded_plan() for _ in range(args.runs)))
        both = sum(r[0] and r[1] for r in runs)
        ok = both >= PASS_RATE * args.runs
        failed += not ok
        print(f"{'plan':<6}{'text':<12}{sum(r[0] for r in runs):>6}{sum(r[1] for r in runs):>7}{both:>6}  "
              f"{'PASS' if ok else 'FAIL'}   (what is left = the plan's open steps, in order)")
        if not ok and args.verbose:
            print(textwrap.indent(next(r[2] for r in runs if not (r[0] and r[1]))[:700], "      | "))
        cases = [*cases, {"id": "plan"}]
    total = len(cases) * len(modes) - (len(modes) - 1 if any(c["id"] == "plan" for c in cases) else 0)
    print(f"\n{total - failed}/{total} case×mode passed (bar: {PASS_RATE:.0%} of {args.runs} runs each)")
    print(f"Actual: {USAGE.get('calls', 0)} model calls, {USAGE.get('input_tokens', 0):,} input + "
          f"{USAGE.get('cache_write_tokens', 0):,} cache-write + {USAGE.get('cache_read_tokens', 0):,} cache-read + "
          f"{USAGE.get('output_tokens', 0):,} output tokens, about ${cost(USAGE):.2f}, "
          f"{time.monotonic() - started:.0f} s.")
    if full_gate and not failed:
        GATE_FILE.parent.mkdir(exist_ok=True)
        GATE_FILE.write_text(json.dumps({"fingerprint": print_, "result": f"{total}/{total} passed at {args.runs} runs",
                                         "at": time.strftime("%Y-%m-%d %H:%M"), "usage": USAGE}))
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=None, help="runs per case (default 3; 10 with --gate)")
    parser.add_argument("--gate", action="store_true", help="the SC-005 gate: 10 runs, skipped when unchanged")
    parser.add_argument("--estimate", action="store_true", help="print the expected calls and cost, then stop")
    parser.add_argument("--force", action="store_true", help="run the gate even if nothing changed")
    parser.add_argument("--cases", default="")
    parser.add_argument("--mode", choices=["text", "screenshot", "both"], default="both")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("-v", "--verbose", action="store_true")
    ns = parser.parse_args()
    ns.runs = ns.runs or (GATE_RUNS if ns.gate else 3)
    sys.exit(asyncio.run(main(ns)))
