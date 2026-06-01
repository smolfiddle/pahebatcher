# pahebatcher — AnimePahe Batch Downloader

High-performance terminal tool for [AnimePahe](https://animepahe.pw). Automates discovering, downloading, and streaming anime series with a parallel HLS engine and persistent segment cache.

## Prerequisites

| Requirement | Purpose | Install |
|---|---|---|
| **[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)** | Cloudflare bypass | `docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr` |
| **[FFmpeg](https://ffmpeg.org/)** | TS → MP4 remux | `sudo apt install ffmpeg` |
| **[MPV](https://mpv.io/)** (optional) | Streaming mode | `sudo apt install mpv` |
| **Python 3.11+** | Runtime | `python3 --version` |

## Installation

```bash
git clone https://github.com/smolfiddle/pahebatcher.git
cd pahebatcher
```

### Option A: Makefile (zero-config)

```bash
make run      # auto-creates venv, installs, launches
make test     # run tests
make lint     # ruff check
```

### Option B: pipx (global install)

```bash
pipx install .
pahebatcher
```

### Option C: pip editable (dev)

```bash
pip install -e ".[dev]"
pahebatcher
```

## Python Dependencies

| Package | Version | Purpose |
|---|---|---|
| `rich` | ≥13.9 | Terminal UI — progress bars, panels, tables, live dashboards |
| `aiohttp` | ≥3.10 | Async HTTP — high-performance segment downloads |
| `pycryptodomex` | ≥3.21 | AES-128-CBC decryption for DRM-protected HLS streams |

## Quick Start

```bash
# Interactive wizard (search or paste URL)
pahebatcher

# Full batch: all episodes, 720p, subbed
pahebatcher "URL" --all -q 720

# Season 1, dubbed, 1080p, custom output
pahebatcher "URL" --range 1-12 --audio eng -q 1080 -o ~/anime

# Just list episodes
pahebatcher "URL" --list

# Stream in MPV with SUB/DUB switching
pahebatcher "URL" --stream -q 1080

# Latest 3 episodes
pahebatcher "URL" --latest 3
```

## CLI Reference

| Flag | Description | Default |
|---|---|---|
| `URL` | AnimePahe series URL. Launches interactive search if omitted. | — |
| `-a`, `--all` | Download every episode | — |
| `-r`, `--range RANGE` | Episodes: `1-12`, `1,4,7`, `13-` | — |
| `-n`, `--latest N` | Latest N episodes | — |
| `-s`, `--stream` | Stream via MPV | — |
| `-l`, `--list` | Scan and print episode table, then exit | — |
| `-q`, `--quality Q` | Resolution: `360`, `720`, `1080` | `1080` |
| `-o`, `--output DIR` | Output directory | `./downloads` |
| `-j`, `--parallel N` | Concurrent episode downloads (1–6) | `2` |
| `-w`, `--workers N` | HLS segment fetchers per episode (8–32) | `24` |
| `--audio LANG` | Audio: `jpn` (subbed) or `eng` (dubbed) | `jpn` |
| `--keep-temp` | Keep raw `.ts` segment files after muxing | off |
| `--verbose`, `-v` | Enable debug logging | off |

## Architecture

### Two-stage prefetch pipeline

```
Stage 1 — Resolver (serial, FlareSolverr)
  Fetches play pages, extracts Kwik URLs, resolves to M3U8 manifests.

Stage 2 — Downloaders (concurrent, N workers)
  Fetches HLS segments in parallel, decrypts AES-128, writes atomically.
```

Resolver runs ahead of downloaders via `asyncio.Queue` (`max_parallel + 2` capacity).

### Segment Store

```
pahe_cache/
  Series_Title_<uuid>/
    session.json          ← metadata
    Ep_001_JPN/
      000000.ts
      000001.ts
      ...
```

- **Atomic writes**: `.tmp` → rename to `.ts`
- **Resume-safe**: reads `done_indices()`, skips downloaded
- **Orphan cleanup**: >24h old cache auto-cleaned

### Kwik → M3U8 Resolution

1. Fetch play page via FlareSolverr
2. Parse resolution buttons (`data-src`, `data-resolution`, `data-audio`)
3. Fetch Kwik page directly with Referer bypass
4. Extract M3U8: direct regex → JS deobfuscation → `<source>` tag
5. Parse M3U8: follow variants, handle AES-128 keys

### Retry Policy

5 attempts per request, exponential backoff: `0.5s × 2^attempt`.

## Package Structure

```
src/pahebatcher/
├── __init__.py, __main__.py
├── main.py              # CLI + entry point
├── models.py            # EpisodeInfo, AnimeInfo, StreamInfo, AppContext
├── config.py            # Constants
├── utils.py             # sanitize, ep_prefix, fmt_bytes
├── tls.py               # Hardened SSL context
├── cache.py             # Async-safe TTLCache
├── store.py             # SegmentStore
├── http.py              # HttpClient (aiohttp manager)
├── solver.py            # FlareSolverr (instance-based)
├── extract/
│   ├── kwik.py          # JsPacker, Kwik resolution
│   ├── m3u8.py          # HLS manifest parser
│   └── scanner.py       # AnimePahe scanner
├── downloader.py        # EpisodeDownloader, BatchOrchestrator
├── stream.py            # MPV stream player
├── sessions.py          # Session manager
└── ui/
    ├── console.py       # Rich console, banner
    ├── dashboard.py     # Live progress dashboard
    ├── tables.py        # Table rendering helpers
    └── prompts.py       # Episode selection, wizard, confirmations
```

## Development

```bash
# Option A: Makefile
make test       # run tests
make lint       # ruff check
make typecheck  # mypy strict
make clean      # remove venv + cache

# Option B: manual
pip install -e ".[dev]"
pytest tests/ -v
ruff check src/
mypy src/
```

## License

MIT License. See [LICENSE](LICENSE) for details.
