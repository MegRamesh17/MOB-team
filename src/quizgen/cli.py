"""
Command line driver.

    quizgen ingest              PDFs  -> chunks
    quizgen generate            chunks -> candidate questions (PendingReview)
    quizgen review              approve / reject candidates
    quizgen quiz                take an adaptive quiz
    quizgen simulate            fake a learner's answers to exercise adaptivity
    quizgen status              bank + learner state
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path
from typing import List, Optional

from .adaptive import build_quiz, coverage_gaps, weak_topics
from .bank import Bank, utcnow
from .config import CONFIG, DOCUMENT_DIR, OUTPUT_DIR
from .grading import grade_one, score_attempt
from .ingest import files_without_headings, ingest_directory
from .retrieval import BM25
from .roles import derive_role_profiles, write_profiles
from .validators import validate
from .llm.base import get_generator, get_judge
from .models import Question, QuestionType, ReviewStatus, stable_id

BAR = "-" * 74


def _bank() -> Bank:
    return Bank(CONFIG.db_path)


def cmd_ingest(args: argparse.Namespace) -> int:
    directory = Path(args.pdf_dir) if args.pdf_dir else DOCUMENT_DIR
    if args.source == "blob":
        from .sources import chunks_from_blob_container
        targets = (
            [args.container]
            if args.container
            else ["{} ({})".format(name, scope) for name, scope in CONFIG.document_containers]
        )
        print("Reading PDFs via src/pdf_extractor.py from: {}".format(", ".join(targets)))
        try:
            chunks = chunks_from_blob_container(args.container)
        except RuntimeError as exc:
            print("\n{}".format(exc), file=sys.stderr)
            return 1
    else:
        print("Reading documents from {}".format(directory))
        chunks = ingest_directory(directory, role_scope=getattr(args, "role_scope", "ALL"))
    with _bank() as bank:
        bank.save_chunks(chunks)

    by_doc = {}
    for c in chunks:
        by_doc.setdefault(c.doc_title, []).append(c)
    for doc, cs in sorted(by_doc.items()):
        print("\n  {}  ({} chunks)".format(doc, len(cs)))
        for topic in sorted({c.topic for c in cs}):
            n = sum(1 for c in cs if c.topic == topic)
            print("      {:<44} {} chunk(s)".format(topic[:44], n))
    print("\n{} chunks stored.".format(len(chunks)))

    flat = files_without_headings()
    if flat:
        print("\nWARNING: no section headings detected in: {}".format(", ".join(flat)))
        print("Topics for those files were derived from keywords instead, which makes")
        print("targeting coarser. If your converter dropped heading lines, restoring")
        print("them is the single highest-value fix for question quality.")
    return 0


def cmd_generate(args: argparse.Namespace) -> int:
    """
    Thin wrapper over quizgen.pipeline. The loop itself lives there so this command
    and the HTTP upload endpoint run identical code — including the validation that
    is now the only gate before a learner sees a question.
    """
    from .pipeline import generate_questions, select_chunks

    with _bank() as bank:
        if not bank.all_chunks():
            print("No chunks yet. Run 'ingest' first.", file=sys.stderr)
            return 1

        chunks, skipped = select_chunks(
            bank,
            topic=args.topic or "",
            scope=args.scope or "",
            regenerate=args.regenerate,
            limit=args.limit,
        )
        if skipped:
            print("Skipping {} chunk(s) that already have questions "
                  "(--regenerate to force).".format(skipped))
        if not chunks:
            print("Nothing to generate from.", file=sys.stderr)
            return 1

        print("Generating from {} chunk(s).".format(len(chunks)))
        print("Generator: {}   (QUIZGEN_PROVIDER={})".format(
            get_generator(bank.all_chunks()).name, CONFIG.provider))

        def report(p):
            if p.error:
                print("  [{:>3}/{}] {:<38} FAILED ({})".format(
                    p.index, p.total, p.chunk.topic[:38], p.error), flush=True)
            else:
                print("  [{:>3}/{}] {:<38} +{} kept, {} rejected so far".format(
                    p.index, p.total, p.chunk.topic[:38], p.kept_in_batch, p.rejected_total),
                    flush=True)

        r = generate_questions(bank, chunks, per_chunk=args.per_chunk, on_progress=report)

        print("\nGenerated {} valid question(s); {} new, {} already in the bank.".format(
            len(r.kept), r.written, len(r.kept) - r.written))

        if r.rejected:
            print("\nRejected {} question(s) before storage:".format(len(r.rejected)))
            for x in r.rejected[:10]:
                print("   - {}".format(x))

        if r.notes:
            print("\n{} question(s) flagged for possible contradiction with other".format(len(r.notes)))
            print("documents. Stored, but the finding is recorded on the question:")
            for qid, findings in list(r.notes.items())[:5]:
                print("   - {}".format(findings[0]))

        if r.failed:
            print("\n{} chunk(s) failed and were skipped — re-run to retry them:".format(len(r.failed)))
            for f in r.failed[:8]:
                print("   - {}".format(f))

        if CONFIG.auto_approve:
            print("\nApproved on save (QUIZGEN_AUTO_APPROVE=true) — servable now.")
            print("The mechanical checks in validators.py were the only gate.")
        else:
            print("\nAll new questions are PendingReview — they cannot be served to a")
            print("learner until approved. Run:  quizgen review")
    return 0


def cmd_corpus(args: argparse.Namespace) -> int:
    """Fetch the vetted sources and index them in Azure AI Search."""
    from .registry import SEED_SOURCES, load_registry, write_registry
    from .search_index import create_index, stats, upload
    from .web import build_corpus

    path = OUTPUT_DIR / "vetted_sources.yaml"
    if not path.exists():
        write_registry(SEED_SOURCES, path)
        print("Seeded a starter registry at {}".format(path))
        print("These are SUGGESTIONS — review and edit before relying on them.\n")

    sources = load_registry(path)
    print("{} vetted source(s) in the registry.\n".format(len(sources)))

    if args.list:
        for s in sources:
            roles = ", ".join(s.roles) if s.roles else "all roles"
            print("  {:<46} {}".format(s.title[:46], roles))
            print("      {}".format(s.url))
        return 0

    print("Fetching...")
    chunks = build_corpus(sources, limit=args.limit)
    if not chunks:
        print("\nNothing fetched.", file=sys.stderr)
        return 1
    print("\n{} chunk(s) from {} source(s).".format(len(chunks), len(sources)))

    if args.no_index:
        print("--no-index: not uploading.")
        return 0

    try:
        name = create_index(recreate=args.recreate)
        print("\nIndex {!r} ready. Embedding and uploading...".format(name))
        count = upload(chunks)
        print("Uploaded {} document(s).".format(count))
        current = stats()
        print("\nIndex now holds {} document(s) across {} topic(s).".format(
            current["documents"], len(current["topics"])))
    except Exception as exc:  # noqa: BLE001
        print("\nIndexing failed: {}: {}".format(type(exc).__name__, exc), file=sys.stderr)
        return 1
    return 0


def cmd_push(args: argparse.Namespace) -> int:
    """Copy the local bank into Azure SQL."""
    from .loader import load, verify

    try:
        counts = load(dry_run=args.dry_run)
    except RuntimeError as exc:
        print("\n{}".format(exc), file=sys.stderr)
        return 1

    if args.dry_run:
        print("Would push:")
        for k, v in counts.items():
            print("   {:<12} {}".format(k, v))
        print("\nRun without --dry-run to actually write.")
        return 0

    print("Pushed to Azure SQL:")
    for k, v in counts.items():
        print("   {:<12} {}".format(k, v))

    print("\nVerifying what landed...")
    try:
        state = verify()
    except Exception as exc:  # noqa: BLE001
        print("   could not verify: {}".format(exc), file=sys.stderr)
        return 1

    for k in ("chunks", "questions", "options", "answer_keys", "approved", "pending"):
        print("   {:<12} {}".format(k, state.get(k)))

    broken = state.get("broken_questions", 0)
    if broken:
        print("\n   WARNING: {} question(s) have no single correct answer.".format(broken))
        print("   Those grade every learner to zero. Investigate before approving.")
        return 1
    print("   {:<12} {}  (every question has exactly one correct answer)".format("broken", 0))
    return 0


def cmd_roles(args: argparse.Namespace) -> int:
    with _bank() as bank:
        chunks = bank.all_chunks()
        if not chunks:
            print("No chunks yet. Run 'ingest' first.", file=sys.stderr)
            return 1

        profiles = derive_role_profiles(chunks)
        path = write_profiles(profiles, OUTPUT_DIR / "role_profiles.yaml")

        print("Derived {} role(s) from the documents:\n".format(len(profiles)))
        for p in profiles:
            print("  {:<14} {:<28} {} documented topic(s)".format(
                p.code, p.title[:28], len(p.documented_topics)))

        print("\nWritten to {}".format(path))
        print(textwrap.fill(
            "Derivation finds which topics the documents tie to each role. It cannot "
            "surface a requirement no document mentions — that is what "
            "inferred_requirements is for, and filling it needs a model. Each inferred "
            "requirement produces RoleKnowledge questions only: barred from asserting "
            "company rules, and always sent to expert review.", 74))
    return 0


def _render(q: Question, index: Optional[int] = None, show_answer: bool = False) -> str:
    head = "Q{}. ".format(index) if index is not None else ""
    lines = [
        "{}[{} | {} | {}]".format(head, q.topic, q.question_type.value, q.difficulty.value),
        textwrap.fill(q.prompt, 74, initial_indent="  ", subsequent_indent="  "),
    ]
    for i, o in enumerate(q.options):
        mark = " *" if (show_answer and o.is_correct) else "  "
        lines.append("   {}{}) {}".format(mark, chr(ord("a") + i), textwrap.shorten(o.text, 66)))
    if q.question_type == QuestionType.FILL_IN_BLANK and show_answer:
        lines.append("    * accepted: {}".format("; ".join(q.accepted_answers)))
    if show_answer and q.source_quote:
        lines.append("    source: {} p.{}".format(q.source_doc_title, q.source_page))
        lines.append(textwrap.fill('"' + textwrap.shorten(q.source_quote, 200) + '"', 70,
                                   initial_indent="            ", subsequent_indent="            "))
    return "\n".join(lines)


def cmd_review(args: argparse.Namespace) -> int:
    with _bank() as bank:
        pending = bank.questions(status=ReviewStatus.PENDING)
        if not pending:
            print("Nothing pending review.")
            return 0

        if args.approve_all:
            bank.set_review_status([q.question_id for q in pending], ReviewStatus.APPROVED)
            print("Approved all {} pending question(s).".format(len(pending)))
            print("\nFine for a dry run. For real content a human must read each one —")
            print("an approved question with a wrong key certifies people on false information.")
            return 0

        print("{} question(s) pending. [a]pprove / [r]eject / [s]kip / [q]uit\n".format(len(pending)))
        approved, rejected = [], []
        for i, q in enumerate(pending, 1):
            print(BAR)
            print(_render(q, i, show_answer=True))
            choice = input("\n  > ").strip().lower()
            if choice.startswith("a"):
                approved.append(q.question_id)
            elif choice.startswith("r"):
                rejected.append(q.question_id)
            elif choice.startswith("q"):
                break

        if approved:
            bank.set_review_status(approved, ReviewStatus.APPROVED)
        if rejected:
            bank.set_review_status(rejected, ReviewStatus.REJECTED)
        print("\nApproved {}, rejected {}.".format(len(approved), len(rejected)))
    return 0


def _ask(q: Question, index: int) -> tuple:
    print(BAR)
    print(_render(q, index))
    if q.question_type == QuestionType.FILL_IN_BLANK:
        return [], input("\n  your answer > ").strip()
    letters = input("\n  your answer (letter{}) > ".format(
        "s, comma separated" if q.question_type == QuestionType.MULTI_SELECT else "")).strip().lower()
    picked = []
    for token in letters.replace(" ", "").split(","):
        if len(token) == 1 and token.isalpha():
            idx = ord(token) - ord("a")
            if 0 <= idx < len(q.options):
                picked.append(q.options[idx].option_id)
    return picked, ""


def cmd_quiz(args: argparse.Namespace) -> int:
    with _bank() as bank:
        try:
            plan = build_quiz(bank, args.learner, length=args.length)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 1

        print(BAR)
        print("Adaptive quiz for {}  ({} questions)".format(args.learner, len(plan.questions)))
        print("Mode: {}".format("REMEDIAL — targeting weak topics" if plan.is_remedial
                                else "BASELINE — no weakness data yet"))
        print(plan.explain())

        judge = get_judge()
        started = utcnow()
        attempt_id = stable_id("attempt", args.learner, started)
        responses = []
        for i, q in enumerate(plan.questions, 1):
            selected, text = _ask(q, i)
            responses.append(grade_one(q, selected, text, judge))

        attempt = score_attempt(args.learner, attempt_id, plan.questions, responses, started)
        bank.save_attempt(attempt)

        print("\n" + BAR)
        print("Score: {:.0f}%  ({}/{})   {}".format(
            attempt.score_percent, attempt.points_awarded, attempt.points_possible,
            "PASS" if attempt.passed else "FAIL (pass mark {:.0f}%)".format(CONFIG.passing_score)))

        wrong = [r for r in attempt.responses if not r.is_correct]
        if wrong:
            print("\nReview these:")
            for r in wrong:
                q = bank.get_question(r.question_id)
                if not q:
                    continue
                print("\n  [{}] {}".format(q.topic, textwrap.shorten(q.prompt, 62)))
                if q.explanation:
                    print("      {}".format(textwrap.shorten(q.explanation, 150)))
                if q.source_doc_title:
                    print("      see: {} p.{}".format(q.source_doc_title, q.source_page))
        _print_learner_state(bank, args.learner)
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    """
    Answer quizzes automatically so the adaptive loop can be watched end to end.

    The simulated learner is deliberately bad at one topic — that is what makes the
    remediation visible across rounds.
    """
    import random

    rng = random.Random(args.seed)
    with _bank() as bank:
        for round_no in range(1, args.rounds + 1):
            try:
                plan = build_quiz(bank, args.learner, length=args.length)
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 1

            started = utcnow()
            attempt_id = stable_id("attempt", args.learner, started, str(round_no))
            responses = []
            for q in plan.questions:
                weak = args.weak_topic.lower() in q.topic.lower()
                # 25% right on the weak topic, 85% elsewhere.
                correct = rng.random() < (0.25 if weak else 0.85)
                if q.question_type == QuestionType.FILL_IN_BLANK:
                    text = q.accepted_answers[0] if (correct and q.accepted_answers) else "not sure"
                    responses.append(grade_one(q, [], text))
                else:
                    ids = q.correct_option_ids()
                    wrong_ids = [o.option_id for o in q.options if not o.is_correct]
                    pick = ids[:1] if correct else wrong_ids[:1]
                    responses.append(grade_one(q, pick, ""))

            attempt = score_attempt(args.learner, attempt_id, plan.questions, responses, started)
            bank.save_attempt(attempt)

            share = sum(1 for q in plan.questions if args.weak_topic.lower() in q.topic.lower())
            print("\nRound {}: score {:>3.0f}%   {}/{} questions on '{}'   [{}]".format(
                round_no, attempt.score_percent, share, len(plan.questions),
                args.weak_topic, "remedial" if plan.is_remedial else "baseline"))
            for tp in plan.topic_plans:
                if tp.reason.startswith("weak"):
                    print("     targeting {:<36} {}".format(tp.topic[:36], tp.reason))

        _print_learner_state(bank, args.learner)
        gaps = coverage_gaps(bank, args.learner)
        if gaps:
            print("\nBank is running dry on weak topics — generate more questions for:")
            for topic, n in gaps:
                print("   {:<44} {} unseen question(s) left".format(topic[:44], n))
    return 0


def _print_learner_state(bank: Bank, learner_id: str) -> None:
    mastery = bank.mastery(learner_id)
    if not mastery:
        return
    print("\n" + BAR)
    print("Mastery for {} (after {} attempt(s))".format(learner_id, bank.attempt_count(learner_id)))
    for m in sorted(mastery.values(), key=lambda m: m.accuracy):
        flag = "  <-- weak" if m in weak_topics(mastery) else ""
        print("   {:<40} {:>3.0f}%  {:<9} ({}/{}){}".format(
            m.topic[:40], m.accuracy * 100, m.level, m.correct, m.answered, flag))


def cmd_status(args: argparse.Namespace) -> int:
    with _bank() as bank:
        s = bank.stats()
        print("Bank: {}".format(CONFIG.db_path))
        print("  chunks     {}".format(s["chunks"]))
        print("  questions  {}  (approved {}, pending {}, rejected {})".format(
            s["questions"], s["approved"], s["pending"], s["rejected"]))
        print("  attempts   {}   responses {}".format(s["attempts"], s["responses"]))
        if args.learner:
            _print_learner_state(bank, args.learner)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="quizgen", description="PDF -> adaptive quizzes")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ingest", help="extract and chunk documents")
    p.add_argument("--pdf-dir", help="with --source local: folder to read (default: data/documents)")
    p.add_argument("--role-scope", default="ALL",
                   help="with --source local: tag this batch for a role (default: ALL, "
                        "company-wide). Blob ingestion takes this from the container.")
    p.add_argument("--source", choices=["blob", "local"], default="blob",
                   help="where documents come from (default: blob)")
    p.add_argument("--container", help="blob container name (with --source blob)")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("generate", help="generate candidate questions")
    p.add_argument("--per-chunk", type=int, default=CONFIG.questions_per_chunk)
    p.add_argument("--topic", help="only this topic")
    p.add_argument("--scope", help="only this role scope (ALL, SDE1, SWE_MANAGER, ...)")
    p.add_argument("--limit", type=int, help="stop after N chunks — use to test cheaply")
    p.add_argument("--regenerate", action="store_true",
                   help="re-generate chunks that already have questions")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("corpus", help="fetch vetted sources into Azure AI Search")
    p.add_argument("--list", action="store_true", help="show the registry and stop")
    p.add_argument("--limit", type=int, help="only the first N sources")
    p.add_argument("--no-index", action="store_true", help="fetch but do not upload")
    p.add_argument("--recreate", action="store_true", help="delete and rebuild the index")
    p.set_defaults(func=cmd_corpus)

    p = sub.add_parser("push", help="copy the local bank into Azure SQL")
    p.add_argument("--dry-run", action="store_true", help="count rows without writing")
    p.set_defaults(func=cmd_push)

    p = sub.add_parser("doctor", help="check .env credentials and deployment names")
    p.set_defaults(func=lambda a: __import__("quizgen.doctor", fromlist=["run"]).run())

    p = sub.add_parser("roles", help="derive role profiles from the documents")
    p.set_defaults(func=cmd_roles)

    p = sub.add_parser("review", help="approve or reject candidates")
    p.add_argument("--approve-all", action="store_true", help="bulk approve (dry runs only)")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("quiz", help="take an adaptive quiz")
    p.add_argument("--learner", default="demo-learner")
    p.add_argument("--length", type=int, default=CONFIG.quiz_length)
    p.set_defaults(func=cmd_quiz)

    p = sub.add_parser("simulate", help="auto-answer quizzes to exercise adaptivity")
    p.add_argument("--learner", default="sim-learner")
    p.add_argument("--rounds", type=int, default=4)
    p.add_argument("--length", type=int, default=CONFIG.quiz_length)
    p.add_argument("--weak-topic", default="Fire Safety")
    p.add_argument("--seed", type=int, default=7)
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("status", help="show bank and learner state")
    p.add_argument("--learner")
    p.set_defaults(func=cmd_status)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
