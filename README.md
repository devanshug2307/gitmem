# 🧠 gitmem — give any git repo a memory

> Your repo's history knows why everything exists. You just can't ask it. Now you can —
> **and the answers never leave your machine.**
>
> Built for **"The Hangover Part AI: Where's My Context?"** — WeMakeDevs × Cognee hackathon, July 2026.
> Track: **Best Use of Cognee Open Source** — self-hosted [cognee](https://github.com/topoteretes/cognee), local graph, local vectors, local embeddings.

```bash
$ gitmem learn                        # the repo's history becomes a knowledge graph
$ gitmem ask "why does dns_fallback.py exist?"

  dns_fallback.py was added because freshly provisioned Cognee Cloud tenants can sit
  behind stale negative DNS caches (1.1.1.1 served NXDOMAIN from before the record
  existed). It resolves over DNS-over-HTTPS and pins via socket.getaddrinfo…
```

![gitmem knowledge graph of a repo's history](docs/graph.png)
*A repo's commit history as a knowledge graph — commits, authors, files, and causes as connected entities. All of it in `<repo>/.gitmem/`, all of it local.*

## Why this exists

Every codebase is an archaeology site. "Why is this flag here?" "What did we decide about auth?" "Who touched the payment retry logic and why?" The answers are in commit messages, docs, and PR descriptions — scattered across years. New teammates burn weeks re-excavating them; the person who knew left in March.

`gitmem` ingests a repo's commit history and docs into a **local cognee knowledge graph** — commits, authors, files, decisions become connected entities — and gives you a memory you can interrogate in plain language.

## Why this had to be the *open-source* track

**Privacy is the feature.** Your commit history is your company's diary — it cannot go to someone else's cloud. gitmem runs on self-hosted open-source cognee:

- **Graph + vector DBs: local files** inside the repo's own `.gitmem/` directory
- **Embeddings: computed on your machine** (fastembed/ONNX — no embedding API at all)
- **Only the LLM reasoning step** calls out (any litellm provider — point it at Ollama and even that stays home)

Cloud memory couldn't tell this story. Open source can. That's why the track choice is intrinsic, not arbitrary.

## The whole memory lifecycle, on your laptop

| Command | Cognee lifecycle call | What it does |
|---|---|---|
| `gitmem learn` | `remember(chapter, dataset_name=…)` | Commit history (in "chapters") + README/docs → local knowledge graph, one dataset per repo |
| `gitmem ask "…"` | `recall(q, datasets=[…])` | Auto-routed graph search over the repo's memory |
| `gitmem improve` | `improve(dataset=…)` | Real enrichment pass — **the dedicated improve() runs fully locally** (self-hosted has the whole lifecycle, no cloud routes required) |
| `gitmem graph` | `visualize_graph(…)` | Renders the repo's knowledge graph to HTML and opens it |
| `gitmem forget --yes` | `forget(dataset=…)` | The right to be forgotten, for codebases: wipe the repo's memory, provably |

## Quickstart

```bash
git clone https://github.com/devanshug2307/gitmem && cd gitmem
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env         # add a free Gemini key (60s at aistudio.google.com/apikey)

cd /path/to/any/git/repo
python /path/to/gitmem/gitmem.py learn
python /path/to/gitmem/gitmem.py ask "what is this repo about and what changed recently?"
```

All state lands in `<repo>/.gitmem/` — delete the folder (or `gitmem forget --yes`) and the memory is gone.

## The party trick

Run it on cognee's own repository — give the memory engine a memory of itself:

```bash
git clone --depth 50 https://github.com/topoteretes/cognee /tmp/cognee && cd /tmp/cognee
python /path/to/gitmem/gitmem.py learn --commits 36
python /path/to/gitmem/gitmem.py ask "what has the team been working on this week, by whom?"
```

## The war story: teaching litellm a key it didn't speak

The only Google key on hand mid-hackathon was a **Vertex AI express-mode key** (`AQ.…`): it works against `aiplatform.googleapis.com`, but litellm's `gemini/` provider targets a different endpoint entirely, and its `vertex_ai/` provider demands full GCP credentials and a project id. Four dead ends later, the fix was to stop fighting the adapter and become one: [`vertex_express.py`](vertex_express.py) monkeypatches `litellm.acompletion` in-process and translates `gemini/*` calls — messages, system prompts, and instructor's `json_mode` structured outputs (`response_format` → `responseMimeType: application/json`) — into raw Vertex `generateContent` requests, wrapping replies back into `litellm.ModelResponse`. ~130 lines; cognee's whole extraction pipeline runs through it untouched. Set `VERTEX_EXPRESS_KEY` to activate; without it, gitmem uses stock litellm with any normal key.

## Design notes

- **One dataset per repo** (`gitmem_<name>`) — isolation, routing, and surgical deletion for free.
- **History "chapters"** (12 commits each) instead of one-commit-per-document — respects free-tier LLM rate limits while keeping the graph rich (authors, files, and causes still resolve into entities across chapters).
- **`.gitmem/` inside the repo** — the memory lives with the code it describes; `system_root_directory`/`data_root_directory` are pointed there so nothing touches global state.
- Single file, stdlib + cognee only. ~200 lines. Read it in one sitting: [`gitmem.py`](gitmem.py).

## AI assistance disclosure

Built with Claude Code (Claude Fable 5) as pair programmer, under human direction. Disclosed per hackathon rules.

## Team

Built by **Devanshu** ([litelae@gmail.com](mailto:litelae@gmail.com)) and **Divy Goyal**. Sibling submission (different project, different track): [BlackoutOps](https://github.com/devanshug2307/blackoutops) — Cognee **Cloud** track.

## License

[MIT](LICENSE)
