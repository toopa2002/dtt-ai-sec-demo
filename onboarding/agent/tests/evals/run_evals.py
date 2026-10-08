"""Diagnosis evals for the AWS SaaS playbook (SC-005): does the agent's first reply name the cause and the side?

  uv run --project onboarding/agent --group evals python onboarding/agent/tests/evals/run_evals.py [--runs 10]
      [--cases F1,F4] [--mode text|screenshot|both] [--concurrency 4]

Each case in aws_saas_failures/cases.yaml is a participant pasting an error (text) or uploading it (a PNG rendered
from the same text). The turn runs through the real loop and real Claude Haiku on Bedrock; SailPoint tools are
replaced by a fake that returns the same error, so nothing leaves the machine except the Bedrock call. A case passes
when at least 90% of its runs (9/10 by default) name the expected side and match one of its cause patterns.
Exit code 1 when any case misses the bar.
"""

import argparse
import asyncio
import base64
import io
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

import yaml

from onboarding_agent import loop, playbooks
from onboarding_agent.tools.session import SessionTools

HERE = Path(__file__).parent
CASES = HERE / "aws_saas_failures"
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


async def once(case: dict, mode: str) -> tuple[bool, bool, str]:
    events: list[dict] = []

    async def emit(e: dict) -> None:
        events.append(e)

    p = payload(case, mode)
    pb = playbooks.for_session(p["session"])
    await loop.run_turn(p, pb, FailingIsc(case), SessionTools(emit, p["ordered_by"]["role"]), emit)  # type: ignore[arg-type]
    reply = next((e["text"] for e in events if e["type"] == "final"), "")
    side_ok = bool(SIDE[case["side"]].search(reply))
    cause_ok = any(re.search(c, reply, re.I) for c in case["cause"])
    return side_ok, cause_ok, reply


async def main(args: argparse.Namespace) -> int:
    cases = yaml.safe_load((CASES / "cases.yaml").read_text())
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    modes = ["text", "screenshot"] if args.mode == "both" else [args.mode]
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
    total = len(cases) * len(modes)
    print(f"\n{total - failed}/{total} case×mode passed (bar: {PASS_RATE:.0%} of {args.runs} runs each)")
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--cases", default="")
    parser.add_argument("--mode", choices=["text", "screenshot", "both"], default="both")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("-v", "--verbose", action="store_true")
    sys.exit(asyncio.run(main(parser.parse_args())))
