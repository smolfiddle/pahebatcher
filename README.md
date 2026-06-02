# pahebatcher

High-performance terminal tool for batch-downloading and streaming anime from [AnimePahe](https://animepahe.pw). Features a parallel HLS engine with segment-level crash recovery, Rich-powered live dashboard, and MPV streaming with mid-playback SUB/DUB switching.

![Version](https://img.shields.io/badge/version-3.0.0-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Table of Contents

- [About](#about)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Feature Tour](#feature-tour)
- [CLI Reference](#cli-reference)
- [Architecture](#architecture)
- [Package Structure](#package-structure)
- [Development](#development)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

---

## About

Pahebatcher is a terminal application for batch-downloading anime from [AnimePahe](https://animepahe.pw). It supports parallel episode downloads with per-episode HLS segment concurrency, segment-level crash recovery, interactive episode selection, and MPV streaming with mid-playback audio track switching.

Key characteristics:

- **Single-site focus.** Pahebatcher targets AnimePahe exclusively and does not support other sources.
- **Self-hosted infrastructure.** Cloudflare bypass uses a local FlareSolverr instance via Docker. All traffic stays on your machine — no third-party proxies.
- **Crash-safe downloads.** HLS segments are written atomically. A mid-download interruption picks up at the exact segment where it left off, without re-downloading completed work.
- **Concurrent pipeline.** A two-stage prefetch architecture resolves stream URLs ahead of downloaders via an `asyncio.Queue`, eliminating idle time between episodes. Episodes download in parallel (configurable 1–6), with per-episode segment concurrency (configurable 8–32).
- **Rich terminal UI.** Progress dashboard shows all episodes simultaneously with per-episode segment counts, transfer speeds, ETAs, file sizes, and color-coded state transitions. Interactive episode selection includes range input, a toggle checklist, and "latest N" mode.
- **MPV streaming.** Episodes can be streamed directly without downloading. A live playback panel shows the current episode and playlist position. Audio tracks can be toggled between SUB and DUB mid-session.
- **Session management.** Previous download sessions can be resumed, deleted, or cleared from the cache. Cached segments are reused on restart.
- **MIT licensed.** Free to use, modify, and redistribute.

---

## Prerequisites

| Requirement | Purpose | Install |
|---|---|---|
| **[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)** | Cloudflare bypass (headless Chromium) | `docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr` |
| **[FFmpeg](https://ffmpeg.org/)** | TS segment concatenation into MP4 | `sudo apt install ffmpeg` (Linux) / `brew install ffmpeg` (macOS) |
| **[MPV](https://mpv.io/)** | Streaming mode only | `sudo apt install mpv` / `brew install mpv` |
| **Python 3.11+** | Runtime | `python3 --version` |

FlareSolverr must be running before pahebatcher starts. The tool checks reachability on launch and prints the exact Docker command if it cannot connect. The default URL is `http://localhost:8191/v1`; override with the `FLARESOLVERR_URL` environment variable.

---

## Installation

```bash
git clone https://github.com/smolfiddle/pahebatcher.git
cd pahebatcher
```

### Option A: Makefile (zero-config)

```bash
make run        # auto-creates venv, installs dependencies, launches interactive wizard
make test       # run all 88 tests
make lint       # ruff check
make typecheck  # mypy strict
```

### Option B: pipx (isolated global install)

```bash
pipx install .
pahebatcher
```

### Option C: pip editable (development install)

```bash
pip install -e ".[dev]"
pahebatcher
```

All three methods produce the `pahebatcher` command. You can also run via `python -m pahebatcher`.

---

## Quick Start

```bash
# Interactive wizard -- search for a series or paste a URL
pahebatcher

# Download entire series, 720p, Japanese audio, 2 concurrent episodes
pahebatcher "https://animepahe.ru/anime/<uuid>" --all -q 720

# Download episodes 1 through 12, English dub, 1080p, custom output directory
pahebatcher "https://animepahe.ru/anime/<uuid>" --range 1-12 --audio eng -q 1080 -o ~/anime

# Download only the 3 most recently aired episodes
pahebatcher "https://animepahe.ru/anime/<uuid>" --latest 3

# List all episodes and exit (no download)
pahebatcher "https://animepahe.ru/anime/<uuid>" --list

# Stream episodes in MPV with on-the-fly SUB/DUB switching
pahebatcher "https://animepahe.ru/anime/<uuid>" --stream -q 1080

# Maximum throughput: 4 concurrent episodes, 32 HLS workers per episode
pahebatcher "https://animepahe.ru/anime/<uuid>" --all -q 1080 -j 4 -w 32

# Enable debug logging for troubleshooting
pahebatcher "https://animepahe.ru/anime/<uuid>" --all --verbose
```

Output files are saved as `Ep 001 - Episode Title.mp4` in the output directory (default: `./downloads/<series_name>/`).

---

## Feature Tour

### Download Mode

The core workflow: scan a series, select episodes, configure settings, download.

**Episode selection** offers five modes accessible from the interactive wizard or CLI flags:

| Mode | CLI flag | Interactive | Description |
|---|---|---|---|
| All | `--all` | Press `A` | Download every episode in the series |
| Range | `--range 1-12` | Press `R` | Specify with `1-12`, `1,4,7`, `13-` (open-ended), or mixed `1-6,10,14-` |
| Toggle checklist | — | Press `L` | Interactive table; toggle individual episodes with numbers, `a`=select all, `n`=deselect all, `done`=confirm |
| Latest N | `--latest 3` | Press `N` | Grab the most recent N episodes |
| Skip | — | Press `S` | Return to action menu without selecting |

**Settings wizard** (interactive mode only) prompts for:
- Quality (360p / 720p / 1080p) with estimated size per episode
- Audio language (SUB Japanese / DUB English)
- Output directory
- Concurrency: number of simultaneous episode downloads (1-6)
- HLS segment workers per episode (8-32)

**Download dashboard** shows every episode simultaneously with live per-episode metrics: segment counter (M of N), percentage, transfer speed, ETA, and file size. Each episode transitions through color-coded states: resolving (cyan) -> queued (dim cyan) -> downloading (bold white) -> remuxing (yellow) -> done (green checkmark) / fail (red cross).

**Segment-level crash recovery** is unique to pahebatcher. HLS segments are written atomically (.tmp file renamed to .ts after write completes). On restart, the tool reads `done_indices()` and only fetches missing segments. A mid-episode power failure costs zero progress. Already-completed MP4 files in the output directory are skipped entirely.

### Stream Mode

Launches MPV with the resolved M3U8 URL and authentication headers. Displays a live "Now Playing" panel with episode title, audio track, quality, and playlist position.

**Post-episode navigation:**
- `N` / `P` — next / previous episode in playlist
- `A` — toggle SUB / DUB audio track mid-session (reloads playlist with new audio lane)
- `R` — replay current episode
- `S` — jump to any episode by number
- `Q` — quit

Post-episode controls appear after MPV closes, offering navigation, audio switching, replay, and episode selection.

### Session Manager

Accessible from the main menu (option 3). Lists all cached sessions with:
- Anime title and URL
- Episode count and segment count
- Total cache size on disk
- Status (Paused)

**Actions:** Resume (restarts tool with that series URL), Delete (removes single session cache), Clear All (wipes entire `pahe_cache/` directory).

### Search Mode

Running `pahebatcher` without a URL opens interactive search. Type an anime title, browse results in a table (title, type, year, episodes, score), select by number. The tool auto-discovers SUB and DUB variants of the selected series.

---

## CLI Reference

```
pahebatcher [URL] [options]
```

### Episode selection (mutually exclusive)

| Flag | Shorthand | Description |
|---|---|---|
| `--all` | `-a` | Download every episode |
| `--range RANGE` | `-r RANGE` | Episode range: `1-12`, `1,4,7`, `13-` |
| `--latest N` | `-n N` | Latest N episodes |
| `--stream` | `-s` | Stream via MPV instead of downloading |

### Display and output

| Flag | Shorthand | Description | Default |
|---|---|---|---|
| `--list` | `-l` | Scan series, print episode table, exit | off |
| `--output DIR` | `-o DIR` | Output directory (series name appended) | `./downloads` |
| `--quality Q` | `-q Q` | Resolution: `360`, `720`, `1080` | `1080` |
| `--audio LANG` | — | Audio track: `jpn` (subbed), `eng` (dubbed) | `jpn` |
| `--parallel N` | `-j N` | Concurrent episode downloads (1-6) | `2` |
| `--workers N` | `-w N` | HLS segment fetchers per episode (8-32) | `24` |
| `--keep-temp` | — | Keep raw `.ts` files after muxing | off |
| `--verbose` | `-v` | Enable debug-level logging | off |

### Concurrency tuning

The two concurrency flags control different layers of parallelism:

- `-j` / `--parallel`: How many episodes download at once. Default 2. The resolver stage runs on a single FlareSolverr instance (serial), so values above 3-4 rarely improve throughput. Increase if your internet connection has significant headroom.

- `-w` / `--workers`: How many HLS segments each episode fetches concurrently. Default 24. HLS segments are small (~100 KB each), so high concurrency saturates residential connections efficiently. Lower to 8-12 if you see frequent segment failures from CDN rate limiting.

The combined maximum concurrent TCP streams is `parallel * workers` (default: 2 * 24 = 48). At `-j 4 -w 32`, this reaches 128 streams.

---

## Architecture

### Two-Stage Prefetch Pipeline

```
  +-------------------------------------------------------------+
  |  Stage 1 -- Resolver (serial, 1 worker)                     |
  |  FlareSolverr fetches play pages, extracts Kwik URLs,       |
  |  resolves them to M3U8 manifests. One at a time because     |
  |  FlareSolverr uses a single Chromium tab.                   |
  +-------------------------------------------------------------+
                            |
                            v  (asyncio.Queue, capacity = max_parallel + 2)
  +-------------------------------------------------------------+
  |  Stage 2 -- Downloaders (concurrent, N workers)             |
  |  Each downloads HLS segments in parallel (24 workers per    |
  |  episode), decrypts AES-128 if needed, writes atomically    |
  |  to the segment store, then muxes via ffmpeg.               |
  +-------------------------------------------------------------+
```

The resolver runs slightly ahead of downloaders via `asyncio.Queue` with `max_parallel + 2` capacity. This ensures the next episode is always ready when a downloader finishes -- no idle time between episodes.

### Shared Connection Pool

A single `aiohttp.ClientSession` is created for the entire batch:
- `limit=0` (no global connection cap; semaphores control concurrency)
- `limit_per_host=hls_workers` (per-CDN bounds)
- `keepalive_timeout=45s` (TCP reuse across segments of the same stream)
- 5-minute DNS cache
- TLSv1.2 minimum, forward-secrecy ciphers only, certificate verification required

### Segment Store

```
pahe_cache/
  Spy_x_Family_f1a5749e/
    session.json           -- metadata (title, URL, timestamp)
    Ep_001_JPN/
      000000.ts
      000001.ts
      ...
      concat.txt           -- ffmpeg concat demuxer playlist
    Ep_002_JPN/
      ...
```

- **Atomic writes**: segments written to `.tmp` then renamed to `.ts`. Crash at any point leaves the store consistent.
- **Resume**: on restart, `done_indices()` reads existing segment IDs. Only missing segments are fetched. Completed MP4 files are skipped entirely.
- **Orphan cleanup**: cache directories older than 24 hours without active downloads are removed on exit.

### Kwik to M3U8 Resolution Chain

1. Fetch play page via FlareSolverr
2. Parse resolution menu buttons (`data-src`, `data-resolution`, `data-audio`, `data-fansub`)
3. Select quality: closest available resolution below or equal to user preference. Falls back to highest available if none match.
4. Select audio track: 3-strategy detection -- `data-audio` attribute, CSS class names, text content. Falls back from DUB to SUB automatically when DUB is unavailable.
5. Fetch Kwik page directly with `Referer: https://animepahe.com/` (bypasses Cloudflare without FlareSolverr for this hop)
6. Extract M3U8 URL via 3 strategies: direct regex match, JS deobfuscation (eval-unwrap + `JsPacker` unpacker for `p,a,c,k,e,d` packed scripts), `<source>` tag fallback
7. Parse M3U8: follow `#EXT-X-STREAM-INF` master playlist variants (last = highest quality), extract `#EXT-X-KEY` for AES-128 keys
8. Fetch AES key (cached with 1-hour TTL), download segments, decrypt in-memory, write atomically

### Muxing

FFmpeg concat demuxer (single-pass, fastest): `ffmpeg -f concat -safe 0 -i concat.txt -c copy -movflags +faststart out.mp4`. Falls back to pipe mode (`cat segments | ffmpeg -i pipe:0`) if concat demuxer fails on malformed TS.

### Retry Policy

5 attempts per request with exponential backoff: `0.5s * 2^attempt`. Applied to segment fetches, Kwik resolution, and FlareSolverr API calls. FlareSolverr sessions are auto-recreated on "session not found" errors.

---

## Package Structure

```
src/pahebatcher/
    __init__.py              Version and package metadata
    __main__.py              python -m pahebatcher entry point
    main.py                  CLI argument parsing, service wiring, action dispatch
    models.py                EpisodeInfo, AnimeInfo, StreamInfo, AppContext
    config.py                Constants (workers, retry, timeouts, version)
    utils.py                 sanitize, ep_prefix, fmt_bytes, compact_ep_range
    tls.py                   Hardened SSL context (TLSv1.2+, forward secrecy)
    cache.py                 Async-safe TTLCache with asyncio.Lock and LRU eviction
    store.py                 SegmentStore (atomic writes, ffmpeg assembly)
    http.py                  HttpClient (shared aiohttp session, retry logic)
    solver.py                FlareSolverr client (instance-based, async-native)
    extract/
        kwik.py              JsPacker, Kwik URL extraction, stream resolution
        m3u8.py              HLS manifest parser, AES-128 key extraction
        scanner.py           AnimePahe API scanner, URL validation, variant discovery
    downloader.py            EpisodeDownloader, BatchOrchestrator (2-stage pipeline)
    stream.py                MPV stream player with live panel and SUB/DUB navigation
    sessions.py              Session manager (list, resume, delete, clear)
    ui/
        console.py           Rich console instance and ASCII art banner
        dashboard.py         Live progress dashboard with per-episode state transitions
        tables.py            Table rendering helpers (episodes, search results, summary)
        prompts.py           Episode selection, download confirmation, settings wizard
```

---

## Development

### Quick commands

```bash
make test        # run all 88 tests
make lint        # ruff check
make typecheck   # mypy strict
make clean       # remove venv, caches, build artifacts
```

### Manual

```bash
pip install -e ".[dev]"
pytest tests/ -v              # 88 tests, asyncio auto-mode
ruff check src/               # ALL rule select, target py311
mypy src/                     # strict mode, full type coverage
```

### Test structure

```
tests/
    conftest.py               Fixtures: mock solver, mock http, sample data
    test_cache.py             8 tests: set/get, eviction, expiry, concurrent access
    test_kwik.py              7 tests: JsPacker, M3U8 extraction, resolution buttons
    test_m3u8.py              8 tests: playlist parsing, AES keys, variant detection
    test_models.py            11 tests: dataclass fields, properties, edge cases
    test_prompts.py           11 tests: episode range parsing, noninteractive selection
    test_scanner.py           12 tests: URL validation, episode page parsing, search
    test_sessions.py          4 tests: cache listing, metadata, error handling
    test_store.py             7 tests: segment I/O, atomic writes, assemble, cleanup
    test_utils.py             20 tests: sanitize, ep_prefix, fmt_bytes, compact_ep_range
```

All tests run under `PYTHONPATH=src pytest tests/ -v` or with the package installed via `pip install -e .`.

### Code standards

- **Python 3.11+** with `from __future__ import annotations` throughout
- **mypy strict** -- no untyped defs, no implicit optionals, full type coverage
- **ruff ALL** -- pycodestyle + isort + pep8-naming + pyupgrade + bugbear + simplify + ruff-specific
- **Dependency injection** -- `AppContext` carries config through the call chain; `Solver`, `HttpClient` are constructor-injected
- **Zero global mutable state** -- no module-level caches, no classmethod singletons, no import-time side effects

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `FlareSolverr not responding` | FlareSolverr container not running | `docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr` |
| `No Kwik link found on episode page` | Site structure changed or Cloudflare blocking | Try a different quality value; ensure FlareSolverr has a healthy browser session |
| `Resolution timed out` | FlareSolverr overloaded or slow network | Retry with fewer concurrent episodes (`-j 1`); the resolver retries 5 times per episode |
| Segment download failures | CDN rate limiting | Lower `-w` to 8-12, lower `-j` to 1 |
| `MPV not found` | MPV not installed or not in PATH | Install via package manager or use Download mode instead |
| `ffmpeg` command not found | FFmpeg not installed | `sudo apt install ffmpeg` / `brew install ffmpeg` |
| `AES-128 stream detected` | pycryptodomex not installed (should not occur with `pip install`) | `pip install pycryptodomex` |
| Slow single-episode downloads | Low segment concurrency | Increase `-w` to 24-32; HLS segments are ~100 KB each and benefit from high parallelism |
| Cache directory growing too large | Old sessions accumulating | Use Session Manager (option 3 from main menu) to clear stale entries; >24h orphans are auto-cleaned |

---

## Contributing

Bug reports, feature requests, and pull requests are welcome.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/description`)
3. Install dev dependencies: `pip install -e ".[dev]"`
4. Make changes; ensure `make lint` and `make typecheck` pass
5. Add or update tests; ensure `make test` passes (88 tests, asyncio auto-mode)
6. Commit with a conventional prefix (`fix:`, `feat:`, `refactor:`, `docs:`, `chore:`)
7. Push and open a pull request

Areas particularly open to contribution:
- Persistent config file (`~/.pahebatcher/config.json`)
- PyPI publication pipeline
- Graceful shutdown on Ctrl+C (finish active downloads before exit)
- Additional anime source support
- CI/CD with GitHub Actions
- Arrow-key navigable TUI menus

---

## License

MIT License. See [LICENSE](LICENSE) for full text.

This tool is intended for personal and educational use. Users are responsible for complying with the terms of service of the websites they access. Support official creators.
