#!/usr/bin/env python3
"""gitmem — give any git repo a memory. 100% local, code never leaves your machine.

Built on self-hosted open-source cognee: the repo's commit history and docs become
a local knowledge graph you can interrogate:

    gitmem learn                 # ingest history + docs   -> cognee.remember()
    gitmem ask "why does X exist?"                         -> cognee.recall()
    gitmem improve               # enrichment pass          -> cognee.improve()
    gitmem graph                 # open the knowledge graph -> visualize_graph()
    gitmem forget --yes          # wipe the repo's memory   -> cognee.forget()

Everything (LLM aside) runs and stays local: graph + vectors live in .gitmem/
inside the repo. Nothing is uploaded anywhere. That is the point.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

VERSION = "0.1.0"

# Quiet cognee's structlog console flood (the "[2m2026-…] info …" lines) before
# cognee is ever imported: cognee.shared.logging_utils.setup_logging() reads the
# LOG_LEVEL env var at import time. ERROR keeps real failures on screen while
# hiding info/warning chatter. An explicit LOG_LEVEL from the shell always wins.
_QUIET_LOGS = not os.environ.get("LOG_LEVEL")
os.environ.setdefault("LOG_LEVEL", "ERROR")

# ANSI helpers — disabled when NO_COLOR is set or stdout is not a terminal.
_USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: str) -> str:
    return code if _USE_COLOR else ""


BOLD, DIM, RESET = _c("\033[1m"), _c("\033[2m"), _c("\033[0m")
AMBER, GREEN, RED, BLUE = _c("\033[33m"), _c("\033[32m"), _c("\033[31m"), _c("\033[34m")

CHAPTER_SIZE = 12          # commits per ingested "history chapter"
DOC_CANDIDATES = ["README.md", "CONTRIBUTING.md", "ARCHITECTURE.md", "docs/README.md"]
DOC_CHAR_CAP = 6000


def banner() -> None:
    print(f"\n  🧠 {AMBER}{BOLD}gitmem{RESET} — your repo remembers"
          f"   {DIM}v{VERSION} · 100% local · built on cognee{RESET}")
    print(f"  {DIM}{'─' * 64}{RESET}\n")


def say(tag: str, msg: str, color: str = AMBER, end: str = "\n") -> None:
    print(f"  {color}{BOLD}▸{RESET} {DIM}{tag:<9}{RESET} {msg}", end=end, flush=True)


def hint(msg: str) -> None:
    print(f"\n  {DIM}↳ next: {msg}{RESET}\n")


def fmt_elapsed(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def repo_root(path: str) -> Path:
    out = subprocess.run(
        ["git", "-C", path, "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        sys.exit(f"{RED}not a git repo: {path}{RESET}")
    return Path(out.stdout.strip())


def dataset_for(root: Path) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", root.name.lower()).strip("_") or "repo"
    return f"gitmem_{slug}"


class _TeardownNoiseFilter(logging.Filter):
    """Hide aiohttp's end-of-process teardown chatter (logged at ERROR via the
    asyncio logger when cognee's HTTP session is GC'd after the loop closes).
    Real errors don't match these strings and still print."""

    _NOISY = ("Unclosed client session", "Unclosed connector")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        return not any(n in msg for n in self._NOISY)


def configure_cognee(root: Path):
    """Local-first: all cognee state lives inside the repo's .gitmem directory."""
    state = root / ".gitmem"
    state.mkdir(exist_ok=True)

    from vertex_express import install as install_vertex_bridge

    if install_vertex_bridge():
        say("llm", "vertex express bridge active (AQ.… key via aiplatform)", DIM)

    import cognee  # imported after env is loaded + litellm patched

    if _QUIET_LOGS:
        # Belt and braces: cognee's own load_dotenv(override=True) may re-lower
        # LOG_LEVEL from a .env in the cwd. Raise only the *console* handler
        # back to ERROR — the rotating log file keeps full detail either way.
        noise_filter = _TeardownNoiseFilter()
        # asyncio's logger is where aiohttp's "Unclosed client session" teardown
        # noise is emitted (loop.call_exception_handler) — filter at the logger
        # so it survives any later handler reconfiguration.
        logging.getLogger("asyncio").addFilter(noise_filter)
        logging.getLogger("aiohttp.client").addFilter(noise_filter)
        for h in logging.getLogger().handlers:
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setLevel(max(h.level, logging.ERROR))
                h.addFilter(noise_filter)

    cognee.config.system_root_directory(str(state / "system"))
    cognee.config.data_root_directory(str(state / "data"))
    return cognee


def collect_chapters(root: Path, n_commits: int) -> list[str]:
    log = subprocess.run(
        ["git", "-C", str(root), "log", f"-n{n_commits}",
         "--pretty=format:%h\x1f%an\x1f%ad\x1f%s\x1f%b\x1e", "--date=short", "--name-only"],
        capture_output=True, text=True,
    ).stdout
    commits = []
    for raw in log.split("\x1e"):
        raw = raw.strip()
        if not raw:
            continue
        head, _, files_block = raw.partition("\n")
        parts = (head + "\x1f\x1f\x1f\x1f").split("\x1f")
        h, author, date, subject, body = parts[0], parts[1], parts[2], parts[3], parts[4]
        files = [f for f in files_block.strip().splitlines() if f][:12]
        entry = f"- commit {h} on {date} by {author}: {subject}."
        if body.strip():
            entry += f" Details: {body.strip()[:300]}"
        if files:
            entry += f" Files touched: {', '.join(files)}."
        commits.append(entry)

    chapters = []
    for i in range(0, len(commits), CHAPTER_SIZE):
        chunk = commits[i : i + CHAPTER_SIZE]
        chapters.append(
            f"Git history of repository '{root.name}', chapter {i // CHAPTER_SIZE + 1} "
            f"(most recent first):\n" + "\n".join(chunk)
        )
    return chapters


def collect_docs(root: Path) -> list[str]:
    docs = []
    for rel in DOC_CANDIDATES:
        p = root / rel
        if p.is_file():
            text = p.read_text(errors="replace")[:DOC_CHAR_CAP]
            docs.append(f"Documentation file {rel} of repository '{root.name}':\n{text}")
    return docs[:3]


def item_label(text: str, repo: str) -> str:
    """Short human label for a memory item, derived from its first line."""
    first = text.splitlines()[0].strip().rstrip(":")
    first = first.replace(f"Git history of repository '{repo}', ", "history · ")
    first = first.replace(f" of repository '{repo}'", "")
    first = first.replace("Documentation file ", "docs · ")
    first = first.replace(" (most recent first)", "")
    return first[:72]


def render_answer(text: str, attribution: str) -> None:
    cols = shutil.get_terminal_size((100, 24)).columns
    width = min(90, max(40, cols - 8))
    print(f"\n  {BLUE}╭─{RESET} {BOLD}answer{RESET}")
    for raw in text.strip().splitlines():
        raw = raw.rstrip()
        if not raw:
            print(f"  {BLUE}│{RESET}")
            continue
        for line in textwrap.wrap(raw, width=width):
            print(f"  {BLUE}│{RESET}  {line}")
    print(f"  {BLUE}╰─{RESET} {DIM}{attribution}{RESET}")


async def cmd_learn(args) -> None:
    root = repo_root(args.repo)
    ds = dataset_for(root)
    cognee = configure_cognee(root)
    chapters = collect_chapters(root, args.commits)
    docs = collect_docs(root)
    items = docs + chapters
    say("learn", f"{len(chapters)} history chapters + {len(docs)} docs → "
                 f"dataset {BOLD}{ds}{RESET} {DIM}(local){RESET}")
    t_start = time.monotonic()
    w = len(str(len(items)))
    for i, item in enumerate(items, 1):
        say("remember", f"{DIM}[{i:>{w}}/{len(items)}]{RESET} {item_label(item, root.name)}",
            BLUE, end="")
        t0 = time.monotonic()
        await cognee.remember(item, dataset_name=ds)
        print(f"  {GREEN}✓{RESET} {DIM}{fmt_elapsed(time.monotonic() - t0)}{RESET}")
    say("done", f"{GREEN}{len(items)} memories in {fmt_elapsed(time.monotonic() - t_start)}"
                f" — the repo remembers.{RESET}", GREEN)
    hint('gitmem ask "what is this repo about?"')


async def cmd_ask(args) -> None:
    root = repo_root(args.repo)
    ds = dataset_for(root)
    cognee = configure_cognee(root)
    say("recall", f"asking the memory of {BOLD}{root.name}{RESET}…", BLUE)
    t0 = time.monotonic()
    results = await cognee.recall(args.question, datasets=[ds])
    elapsed = fmt_elapsed(time.monotonic() - t0)
    answer = None
    for r in results:
        d = r if isinstance(r, dict) else getattr(r, "__dict__", {})
        kind = str(d.get("kind") or d.get("search_type") or "")
        if "completion" in kind.lower():
            answer = str(d.get("text") or d.get("value") or "")
            break
    if answer:
        render_answer(answer, f"recalled from graph memory · {ds} · {elapsed}")
        hint("gitmem graph  — see the knowledge graph it answered from")
    else:
        print(f"\n  {DIM}(no answer — this repo has no memory yet){RESET}")
        hint("gitmem learn  — ingest history + docs first")


async def cmd_improve(args) -> None:
    root = repo_root(args.repo)
    cognee = configure_cognee(root)
    say("improve", "running enrichment pass over the repo's memory…", BLUE)
    t0 = time.monotonic()
    await cognee.improve(dataset=dataset_for(root))
    say("done", f"{GREEN}memory enriched in {fmt_elapsed(time.monotonic() - t0)}"
                f" — connections strengthened.{RESET}", GREEN)
    hint('gitmem ask "…"  — answers should be richer now')


async def cmd_forget(args) -> None:
    root = repo_root(args.repo)
    ds = dataset_for(root)
    if not args.yes:
        sys.exit(f"{RED}refusing without --yes: this permanently wipes {ds}{RESET}")
    cognee = configure_cognee(root)
    receipt = await cognee.forget(dataset=ds)
    say("forget", f"{GREEN}receipt: {receipt}{RESET}", GREEN)
    say("forget", "ask it anything now — it honestly won't know.")
    hint("gitmem learn  — teach it again from scratch")


async def cmd_graph(args) -> None:
    root = repo_root(args.repo)
    cognee = configure_cognee(root)
    out = root / ".gitmem" / "graph.html"
    say("graph", "rendering the knowledge graph…", BLUE)
    await cognee.visualize_graph(destination_file_path=str(out), dataset=dataset_for(root))
    say("graph", f"{GREEN}{out}{RESET}", GREEN)
    webbrowser.open(f"file://{out}")
    hint('gitmem ask "why does <thing> exist?"')


def main() -> None:
    banner()
    p = argparse.ArgumentParser(prog="gitmem", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--repo", default=".", help="path inside the target git repo (default: .)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("learn", help="ingest commit history + docs into local memory")
    s.add_argument("--commits", type=int, default=24, help="how many recent commits (default 24)")
    s.set_defaults(fn=cmd_learn)

    s = sub.add_parser("ask", help="ask the repo's memory a question")
    s.add_argument("question")
    s.set_defaults(fn=cmd_ask)

    s = sub.add_parser("improve", help="run cognee's enrichment pass")
    s.set_defaults(fn=cmd_improve)

    s = sub.add_parser("forget", help="wipe this repo's memory (right to be forgotten)")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_forget)

    s = sub.add_parser("graph", help="render + open the knowledge graph")
    s.set_defaults(fn=cmd_graph)

    args = p.parse_args()
    asyncio.run(args.fn(args))


if __name__ == "__main__":
    main()
