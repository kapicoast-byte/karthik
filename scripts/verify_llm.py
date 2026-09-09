#!/usr/bin/env python3
"""Measure the classifier against the labelled sample in eval/messages.json.

This is the §7 acceptance test, not a smoke test. It reports the two numbers
the client cares about - precision and recall - plus the one criterion that is
pass/fail rather than a percentage: whether the model ever invents a reference
or a date that is not in the message.

    python scripts/verify_llm.py                     # run every case
    python scripts/verify_llm.py --limit 6           # a stratified sample
    python scripts/verify_llm.py --list-models       # what the endpoint serves
    python scripts/verify_llm.py --model <id>        # try another model
    python scripts/verify_llm.py --workers 1         # serialise (default 4)

Latency matters as much as accuracy here: a model that needs a minute per
message cannot sit in front of live Slack intake, however well it scores.
Each run is saved to eval/results/ so models can be compared after the fact.

Works against whichever provider LLM_PROVIDER selects, so a free model and a
paid one can be compared on identical inputs.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# .env before importing settings, which reads the environment on import.
dotenv = ROOT / ".env"
if dotenv.exists():
    for line in dotenv.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

from catalogbot.classifier import Classifier  # noqa: E402
from catalogbot.config import settings  # noqa: E402
from catalogbot.llm import ConfigError, LLMError, available_models  # noqa: E402
from catalogbot.models import SlackContext, Verdict  # noqa: E402

OK, BAD, WARN, INFO = "  ok ", " FAIL", " warn", "     "


def fabrications(text: str, draft) -> list[str]:
    """§10: anything in the draft that is not in the message is invented."""
    if draft is None:
        return []
    found = []

    if draft.reference:
        # Compare loosely - the model may reformat B08-XYZ-1234 as B08XYZ1234.
        loose = re.sub(r"[^A-Za-z0-9]", "", draft.reference).upper()
        haystack = re.sub(r"[^A-Za-z0-9]", "", text).upper()
        if loose and loose not in haystack:
            found.append(f"reference {draft.reference!r} is not in the message")

    if draft.due_date is not None:
        # A real date needs some digit run from the message to back it up.
        digits = set(re.findall(r"\d+", text))
        stated = {
            str(draft.due_date.day),
            f"{draft.due_date.day:02d}",
            str(draft.due_date.year),
        }
        if not (digits & stated):
            found.append(f"due date {draft.due_date} has no basis in the message")

    return found


def _stratified(cases: list[dict], limit: int) -> list[dict]:
    """Take a sample spanning every label, not the first N.

    The file is grouped by label, so a plain slice returns only actionable
    messages - and precision measured without a single negative case is not
    precision at all.
    """
    by_label: dict[str, list[dict]] = {}
    for case in cases:
        by_label.setdefault(case["expect"], []).append(case)

    chosen, labels = [], list(by_label)
    while len(chosen) < limit and any(by_label.values()):
        for label in labels:
            if by_label[label] and len(chosen) < limit:
                chosen.append(by_label[label].pop(0))
    return sorted(chosen, key=lambda c: c["id"])


def list_models() -> int:
    try:
        models = available_models()
    except LLMError as error:
        print(f"{BAD} {error}")
        return 2
    print(f"{INFO} {len(models)} free model(s) at {settings.openai_base_url}\n")
    for identifier, note in models:
        print(f"{INFO}   {identifier:<52} {note}")
    print(f"\n{INFO} set one as CLASSIFIER_MODEL in .env, then re-run this script")
    return 0


def _arg(name: str, default):
    if name in sys.argv:
        return type(default)(sys.argv[sys.argv.index(name) + 1])
    return default


def main() -> int:
    if model := _arg("--model", ""):
        settings.classifier_model = model
    if "--list-models" in sys.argv:
        return list_models()

    cases = json.loads((ROOT / "eval" / "messages.json").read_text(encoding="utf-8"))
    if "--limit" in sys.argv:
        cases = _stratified(cases, int(sys.argv[sys.argv.index("--limit") + 1]))

    print(f"{INFO} provider {settings.llm_provider} · model {settings.classifier_model}")
    print(f"{INFO} {len(cases)} labelled messages\n")

    try:
        classifier = Classifier()
    except LLMError as error:
        print(f"{BAD} {error}")
        return 2

    workers = max(1, _arg("--workers", 4))
    print(f"{INFO} {workers} concurrent request(s)\n")

    results, invented, errors = [], [], []
    fatal: ConfigError | None = None

    def run(case: dict):
        context = SlackContext(
            channel_id="C0TEST",
            channel_name=case["channel"],
            sender_id="U0TEST",
            sender_name=case["sender"],
            message_ts=case["id"],
        )
        started = time.monotonic()
        result = classifier.classify(case["text"], context)
        return case, result, time.monotonic() - started

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, case): case for case in cases}
        for future in as_completed(futures):
            case = futures[future]
            try:
                case, result, elapsed = future.result()
            except ConfigError as error:
                fatal = fatal or error
                continue
            except LLMError as error:
                print(f"{BAD} {case['id']}  {str(error)[:96]}")
                errors.append(case["id"])
                continue

            got, want = result.verdict.value, case["expect"]
            results.append((case, got))

            made_up = fabrications(case["text"], result.draft)
            invented += [(case["id"], reason) for reason in made_up]

            mark = OK if got == want else WARN
            title = result.draft.task if result.draft else "—"
            print(
                f"{mark} {case['id']}  want {want:<11} got {got:<11} "
                f"{title[:42]:<42} {elapsed:5.1f}s"
            )
            for reason in made_up:
                print(f"{BAD}       INVENTED: {reason}")

    if fatal is not None:
        print(f"\n{BAD} {fatal}")
        print(f"{INFO} nothing else will work until this is fixed.")
        return 2

    results.sort(key=lambda pair: pair[0]["id"])
    _summarise(results, invented, errors)
    _save(results, invented, errors)
    return 1 if (invented or errors) else 0


def _save(results, invented, errors) -> None:
    """Keep each run, so a model choice can be argued from data later."""
    if not results:
        return  # a run that classified nothing is not a result
    out = ROOT / "eval" / "results"
    out.mkdir(parents=True, exist_ok=True)
    name = settings.classifier_model.replace("/", "_").replace(":", "_")
    (out / f"{name}.json").write_text(
        json.dumps(
            {
                "model": settings.classifier_model,
                "provider": settings.llm_provider,
                "completed": len(results),
                "errors": errors,
                "fabrications": invented,
                "verdicts": {case["id"]: got for case, got in results},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"{INFO} saved to eval/results/{name}.json")


def _summarise(results, invented, errors) -> None:
    print()
    if not results:
        print(f"{BAD} no cases completed")
        return

    # "Filed" means anything that would become a task. Ambiguous is not a
    # failure - §10 asks for a question rather than a guess.
    true_positive = sum(
        1 for c, g in results if c["expect"] == "actionable" and g == Verdict.ACTIONABLE
    )
    false_positive = sum(
        1 for c, g in results if c["expect"] == "not_a_task" and g == Verdict.ACTIONABLE
    )
    missed = sum(
        1 for c, g in results if c["expect"] == "actionable" and g == Verdict.NOT_A_TASK
    )
    real = sum(1 for c, _ in results if c["expect"] == "actionable")
    chatter = sum(1 for c, _ in results if c["expect"] == "not_a_task")

    exact = sum(1 for c, g in results if c["expect"] == g) / len(results)
    precision = true_positive / (true_positive + false_positive or 1)
    recall = true_positive / (real or 1)

    print(f"{INFO} completed            {len(results)} case(s)")
    print(f"{INFO} exact verdict match  {exact:.0%}")
    print(f"{INFO} precision            {precision:.0%}  (target ≥ 90%)")
    print(f"{INFO} recall               {recall:.0%}  (target ≥ 80%)")
    if false_positive:
        print(f"{WARN} {false_positive} chatter message(s) would have become tasks")
    if missed:
        print(f"{WARN} {missed} real task(s) dropped silently")

    # A verdict is only meaningful over a sample that could have disproved it.
    blockers = []
    if errors:
        blockers.append(f"{len(errors)} case(s) errored: {', '.join(errors)}")
    if invented:
        blockers.append(f"{len(invented)} fabrication(s) — this criterion is pass/fail")
    if not chatter:
        blockers.append("no chatter cases ran, so precision is not measurable")
    if not real:
        blockers.append("no actionable cases ran, so recall is not measurable")
    if len(results) < 10:
        blockers.append(f"only {len(results)} case(s) — too few to conclude anything")

    print()
    for blocker in blockers:
        print(f"{BAD} {blocker}")

    if blockers:
        print(f"\n{WARN} no verdict: run the full set once the blockers above clear")
    elif precision >= 0.9 and recall >= 0.8:
        print(f"{OK} meets the §7 acceptance criteria on this sample")
    else:
        print(f"{WARN} below target — tune the prompt, or use a stronger model")


if __name__ == "__main__":
    sys.exit(main())
