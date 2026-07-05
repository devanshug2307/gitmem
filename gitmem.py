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
import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
AMBER, GREEN, RED, BLUE = "\033[33m", "\033[32m", "\033[31m", "\033[34m"

CHAPTER_SIZE = 12          # commits per ingested "history chapter"
DOC_CANDIDATES = ["README.md", "CONTRIBUTING.md", "ARCHITECTURE.md", "docs/README.md"]
DOC_CHAR_CAP = 6000


def say(tag: str, msg: str, color: str = AMBER) -> None:
    print(f"{color}{BOLD}gitmem{RESET} {DIM}{tag}{RESET} {msg}")


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


def configure_cognee(root: Path):
    """Local-first: all cognee state lives inside the repo's .gitmem directory."""
    state = root / ".gitmem"
    state.mkdir(exist_ok=True)

    from vertex_express import install as install_vertex_bridge

    if install_vertex_bridge():
        say("llm", "vertex express bridge active (AQ.… key via aiplatform)", DIM)

    import cognee  # imported after env is loaded + litellm patched

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


async def cmd_learn(args) -> None:
    root = repo_root(args.repo)
    ds = dataset_for(root)
    cognee = configure_cognee(root)
    chapters = collect_chapters(root, args.commits)
    docs = collect_docs(root)
    items = docs + chapters
    say("learn", f"{len(chapters)} history chapters + {len(docs)} docs → dataset {BOLD}{ds}{RESET} (local)")
    for i, item in enumerate(items, 1):
        say("remember", f"[{i}/{len(items)}] {item.splitlines()[0][:80]}…", BLUE)
        await cognee.remember(item, dataset_name=ds)
    say("done", f"{GREEN}the repo has a memory now. Try: gitmem ask \"what is this repo about?\"{RESET}")


async def cmd_ask(args) -> None:
    root = repo_root(args.repo)
    cognee = configure_cognee(root)
    say("recall", f"asking the graph of {root.name}…", BLUE)
    results = await cognee.recall(args.question, datasets=[dataset_for(root)])
    answered = False
    for r in results:
        d = r if isinstance(r, dict) else getattr(r, "__dict__", {})
        kind = str(d.get("kind") or d.get("search_type") or "")
        if "completion" in kind.lower():
            print(f"\n{BOLD}{d.get('text') or d.get('value')}{RESET}\n")
            answered = True
            break
    if not answered:
        print(f"\n{DIM}(no answer — run `gitmem learn` first?){RESET}\n")


async def cmd_improve(args) -> None:
    root = repo_root(args.repo)
    cognee = configure_cognee(root)
    say("improve", "running enrichment pass over the repo's memory…", BLUE)
    await cognee.improve(dataset=dataset_for(root))
    say("done", f"{GREEN}memory enriched — connections strengthened.{RESET}")


async def cmd_forget(args) -> None:
    root = repo_root(args.repo)
    ds = dataset_for(root)
    if not args.yes:
        sys.exit(f"{RED}refusing without --yes: this permanently wipes {ds}{RESET}")
    cognee = configure_cognee(root)
    receipt = await cognee.forget(dataset=ds)
    say("forget", f"{GREEN}receipt: {receipt}{RESET}")
    say("forget", "ask it anything now — it honestly won't know.")


async def cmd_graph(args) -> None:
    root = repo_root(args.repo)
    cognee = configure_cognee(root)
    out = root / ".gitmem" / "graph.html"
    await cognee.visualize_graph(destination_file_path=str(out), dataset=dataset_for(root))
    say("graph", f"{GREEN}{out}{RESET}")
    webbrowser.open(f"file://{out}")


def main() -> None:
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
