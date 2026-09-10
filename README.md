# pahebatcher

Terminal tool for batch-downloading and streaming anime from [AnimePahe](https://animepahe.pw). Features a parallel HLS engine with segment-level crash recovery, Rich-powered live dashboard, and MPV streaming with mid-playback SUB/DUB switching.

![Version](https://img.shields.io/badge/version-3.3.0-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)

---

## Table of Contents

- [About](#about)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Feature Tour](#feature-tour)
  - [Download Mode](#download-mode)
  - [Stream Mode](#stream-mode)
  - [Session Manager](#session-manager)
  - [Search Mode](#search-mode)
  - [Watchlist](#watchlist-auto-download-ongoing-anime)
  - [Configuration](#configuration)
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
- **Self-hosted infrastructure.** Cloudflare bypass uses a local FlareSolverr instance via Docker. All traffic stays on your machine — no third-party proxies. You can also inject your browser's `cf_clearance` cookie to skip challenge solving entirely.
- **Atomic segment writes.** HLS segments are written to `.tmp` and renamed to `.ts` after completion. A mid-download interruption picks up at the exact segment where it left off, without re-downloading completed work.
- **Concurrent pipeline.** A two-stage prefetch architecture resolves stream URLs ahead of downloaders via an `asyncio.Queue`. A configurable `resolve_ahead` throttle controls how many episodes the resolver stays ahead of downloaders to avoid Cloudflare request bursts. Episodes download in parallel (configurable 1–6), with per-episode segment concurrency (configurable 8–32).
- **Rich terminal UI.** Progress dashboard shows all episodes simultaneously with per-episode segment counts, transfer speeds, ETAs, file sizes, and color-coded state transitions. Interactive episode selection includes range input, a toggle checklist, and "latest N" mode.
- **MPV streaming.** Episodes can be streamed without downloading. A playback panel shows the current episode and playlist position. Audio tracks can be toggled between SUB and DUB mid-session.
- **Session management.** Previous download sessions can be resumed, deleted, or cleared from the cache. Cached segments are reused on restart. Scan results are cached to disk with a configurable TTL so re-running the tool skips all AnimePahe API calls for fresh data.
- **Persistent configuration.** Quality, audio, concurrency, and output directory are saved to `pahebatcher.toml` in the project directory. Set once via the interactive wizard or `pahebatcher config set`, reused on every subsequent run. CLI flags override persisted values when needed. Edit the file directly or use the `config` subcommand.
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
make run          # interactive wizard
make run "URL"    # skip search, go directly to series
make help         # show all targets
make config-show  # display current settings
make watchlist-list  # list watchlist
make watchlist-check # check watchlist for new episodes
make test         # run all 195 tests
make lint         # ruff check (0 errors)
make typecheck    # mypy strict (0 errors)
make benchmark    # full coherence benchmark
make help         # show all targets
```

After first run, set persistent defaults:

```bash
make config-show
venv/bin/python -m pahebatcher config set quality 720
venv/bin/python -m pahebatcher config set audio_lang eng
```

Pass a URL directly (positional or via `URL=`):

```bash
make run "https://animepahe.pw/anime/<uuid>"
make run URL="https://animepahe.pw/anime/<uuid>" ARGS="--all -q 720"
```

### Option B: pipx (isolated global install)

```bash
pipx install .
pahebatcher
```

`pipx` installs `pahebatcher` into an isolated venv at `~/.local/share/pipx/venvs/pahebatcher` and symlinks `~/.local/bin/pahebatcher`. That venv **is not auto-updated** when you `git pull` / `git checkout`.

**Updating after `git pull` or switching branches (e.g. `feature/watchlist`):**

```bash
# from the repo root, after git pull / git checkout:
pipx install . --force && hash -r
pahebatcher --help | grep watchlist   # should list watchlist commands
# alternative (same effect):
pipx reinstall pahebatcher
```

If you skip this, `pahebatcher watchlist check` will say `unrecognized arguments: check` (you're still running the old 3.0.0 binary) while `make watchlist-check` works (it uses `venv`).

### Option C: pip editable (development install)

```bash
pip install -e ".[dev]"
pahebatcher
# no --force needed: edits to src/ are live; only reinstall if pyproject.toml changes
```

All three methods produce the `pahebatcher` command. You can also run via `python -m pahebatcher`.

> **Which binary am I running?**
> ```bash
> which -a pahebatcher          # pipx → ~/.local/bin/pahebatcher, venv → ./venv/bin/pahebatcher
> pahebatcher --help | grep watchlist        # global: should list watchlist if up-to-date
> make watchlist-list            # always uses venv → correct for your checkout
> venv/bin/pahebatcher watchlist list        # direct venv binary
> venv/bin/python -m pahebatcher watchlist list  # most explicit, never stale
> ```
> If global is stale, use the `venv`/`make` form or refresh pipx as above.

---

## Quick Start

```bash
# Interactive wizard -- search for a series or paste a URL
pahebatcher

# Download entire series, 720p, Japanese audio, 2 concurrent episodes
pahebatcher "https://animepahe.pw/anime/<uuid>" --all -q 720

# Download episodes 1 through 12, English dub, 1080p, custom output directory
pahebatcher "https://animepahe.pw/anime/<uuid>" --range 1-12 --audio eng -q 1080 -o ~/anime

# Download only the 3 most recently aired episodes
pahebatcher "https://animepahe.pw/anime/<uuid>" --latest 3

# List all episodes and exit (no download)
pahebatcher "https://animepahe.pw/anime/<uuid>" --list

# Stream episodes in MPV with on-the-fly SUB/DUB switching
pahebatcher "https://animepahe.pw/anime/<uuid>" --stream -q 1080

# 4 concurrent episodes, 32 HLS workers per episode
pahebatcher "https://animepahe.pw/anime/<uuid>" --all -q 1080 -j 4 -w 32

# Save default preferences so you don't need flags every time
pahebatcher config set quality 720

# Watchlist — follow a weekly airing show without re-running manually
pahebatcher watchlist add https://animepahe.pw/anime/<uuid> -q 1080 --audio jpn -o ~/anime
pahebatcher wl list                    # wl/w = shorthand for watchlist
pahebatcher check                      # shorthand for watchlist check (also: wl check, wl c, sync)

# Enable debug logging for troubleshooting
pahebatcher "https://animepahe.pw/anime/<uuid>" --all --verbose
```

Output files are saved as `Ep 001 - Episode Title.mp4` in the output directory (default: `./downloads/<series_name>/`).

Watchlist state lives at `watchlist.json` (cwd, git-ignored). Delete it or `watchlist remove` to stop tracking. Override location with `WATCHLIST_PATH=/tmp/my.json`.

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

**Settings wizard** (interactive mode only) prompts for quality, audio language, output directory, and concurrency. Choices are automatically persisted to `pahebatcher.toml` and reused on future runs — run the wizard once, no need to reconfigure on subsequent sessions.

**Download dashboard** shows every episode simultaneously with live per-episode metrics: segment counter (M of N), percentage, transfer speed, ETA, and file size. Each episode transitions through color-coded states: resolving (cyan) -> queued (dim cyan) -> downloading (bold white) -> remuxing (yellow) -> done (green checkmark) / fail (red cross).

**Segment-level crash recovery.** HLS segments are written atomically (.tmp file renamed to .ts after write completes). On restart, the tool reads `done_indices()` and only fetches missing segments. Already-completed MP4 files in the output directory are skipped entirely.

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

### Watchlist (Auto-Download Ongoing Anime)

Track weekly airing shows without re-running the tool manually. Add a series once with your preferred quality/audio/output, then run a single `check` command (e.g. from cron) — it scans every watched anime, downloads only episodes not already on disk, resumes partial downloads via the existing segment store, and is safe to run repeatedly.

**Workflow:**

1. `add` validates the AnimePahe URL (`scanner.py:30`), normalizes to `https://<host>/anime/<uuid>`, and saves prefs. Without flags it inherits `pahebatcher.toml` (`config_manager.py:15`).
2. `list`/`show`/`remove`/`reset` manage state. `show`/`remove`/`reset` accept `1`-based index, full URL, session UUID, or title substring (`watchlist.py:103`).
3. `check` is one-shot: for each entry it `scan`s with `cache_ttl=0` (forces fresh, unlike normal `60` min cache), diffs against files on disk via `BatchOrchestrator._find_existing()` (`downloader.py:154` — `Ep 001` / `Ep_001` prefix, size>0), **plus remembers deleted episodes while the series folder exists** (`downloaded` history `watchlist.py:30`), and reuses the 2-stage pipeline. Already-present MP4s are skipped, partial `.ts` segments are resumed. Running it again immediately downloads nothing.

```bash
# Add a series to the watchlist (mirrors download flags) — long or short
pahebatcher watchlist add https://animepahe.pw/anime/<uuid> -q 1080 --audio jpn -o ~/anime
pahebatcher wl add https://animepahe.pw/anime/<uuid> --audio eng -q 720 -j 2 -w 24 --keep-temp --retry 2  # wl/w = shorthand
pahebatcher w add https://animepahe.pw/anime/<uuid>          # uses pahebatcher.toml defaults

# List / inspect / remove / reset ( --yes skips confirmation for scripts)
pahebatcher watchlist list  # or wl list, wl ls, wl l
pahebatcher wl show 1       # s/info also work: wl s 1
pahebatcher wl show https://animepahe.pw/anime/<uuid>
pahebatcher wl remove 1 --yes   # also rm/r/del
pahebatcher wl reset 1      # also rst/clear — clear deleted-history (re-download deleted)

# Check for new episodes and download (one-shot, cron-friendly)
pahebatcher watchlist check
pahebatcher wl check        # same
pahebatcher wl c            # shortest: wl c
pahebatcher check           # top-level shorthand → watchlist check
pahebatcher sync            # alias for check
pahebatcher wl check --verbose
pahebatcher wl check https://animepahe.pw/anime/<uuid>  # single series

# Make wrappers (same as above, use project venv)
make watchlist-list      # or make run ARGS="wl ls"
make watchlist-check     # or make run ARGS="check" / ARGS="wl c"
```

**State & persistence:**

- File: `watchlist.json` in `cwd` (sibling to `pahebatcher.toml`), git-ignored, survives `pahe_cache` clear and restarts. Atomic write via `.tmp`→`rename` (`watchlist.py:98`). Corrupted JSON is treated as empty.
- Env: `WATCHLIST_PATH=/tmp/custom.json pahebatcher watchlist list` overrides location (useful for tests/cron isolation).
- Entry fields: `url`, `session`, `host`, `title` (auto-filled on `check`), `quality`, `audio_lang`, `output_dir` (base, sanitized title appended as `os.path.join(output_dir, sanitize(title))`), `max_parallel`, `hls_workers`, `keep_temp`, `auto_retry`, `added_at`, `last_checked`, `downloaded` (list of episode numbers ever seen on disk).
- Title is placeholder `session` on `add` and refreshed on first `check` via `scan`. Updating an existing URL keeps `added_at` and preserves a real title and `downloaded` history.

**Cron / systemd (one-shot only — no daemon):**

```bash
# hourly, append logs
0 * * * * cd /path/to/pahebatcher && venv/bin/python -m pahebatcher watchlist check >> watchlist.log 2>&1

# or with make
0 * * * * cd /path/to/pahebatcher && make watchlist-check >> watchlist.log 2>&1

# custom state location
0 * * * * WATCHLIST_PATH=/home/user/.config/pahebatcher/watchlist.json /home/user/pahebatcher/venv/bin/pahebatcher watchlist check >> /tmp/watchlist.log 2>&1
```

Idempotency: second `check` immediately is a no-op. Failures are per-series isolated — one series failing does not abort others; re-run to retry.

**Deleted episodes — coherent low-memory handling:**

`watchlist.json` keeps `downloaded: [1, 2, 3]` — every episode number that was once seen on disk (`watchlist show 1` shows it). This is the *memory* that makes single-file deletion not redownload:

```bash
pahebatcher wl check          # downloads Ep 1,2,3 → downloaded=[1,2,3]
rm downloads/Saga.../Ep_003*.mp4   # free space: delete one file, keep folder
pahebatcher wl check          # → 0 new, 1 skipped (deleted)  (folder exists → remembers)
pahebatcher wl show 1         # Downloaded: 3 eps (1, 2, 3) still remembered
pahebatcher check             # run again → still 0 new, same skip
```

- **While the series folder exists** (`downloads/Saga.../`), any `downloaded` number not on disk is treated as *intentionally deleted* and **skipped**. `check` reports `1 skipped (deleted)` in dim text.
- **When you delete the whole series folder** (`rm -rf downloads/Saga.../`), next `check` prints `Folder not found (.../Saga...) — resetting skip history` (`watchlist.py:456`) and clears `downloaded` to `[]` — all missing episodes become new again. Folder deletion = coherent “forget” signal.
- **Want a deleted episode back without wiping the folder?** `pahebatcher wl reset 1` (or `wl rst 1`, URL/#) clears history for that entry; next `check` will re-download the missing file:
  ```bash
  pahebatcher wl reset 1
  pahebatcher wl check   # → 1 new (Ep 3) redownloaded
  ```
- Backfill: on first `check` with existing files, they are added to `downloaded` automatically, so old entries migrate without manual edit.

**Behavior vs normal download:**

- Reuses `AnimePaheScanner`, `BatchOrchestrator`, `SegmentStore` — no new download logic.
- `check` forces `cache_ttl=0` so new episodes are seen instantly (normal `pahebatcher [URL]` respects `cache_ttl=60`).
- Audio fallback mirrors download: `get_variant(num, audio_lang)` or first variant.
- No notifications, daemon, or scheduler — user wires to `cron`.

### Configuration

Settings are persisted at `pahebatcher.toml` in the current directory and loaded on every run. Saved defaults apply to every session, CLI flags override them, and the interactive wizard auto-saves whatever is chosen.

Manage settings from the command line:

```bash
pahebatcher config show                 # display current values
pahebatcher config set quality 720      # set default quality
pahebatcher config set audio_lang eng   # set default audio to DUB
pahebatcher config set max_parallel 4     # set default concurrency
pahebatcher config set hls_workers 16     # set default segment workers
pahebatcher config set output_dir ~/anime # set default output directory
pahebatcher config set resolve_ahead 1    # serial resolution to avoid Cloudflare bursts
pahebatcher config set cache_ttl 120      # cache scan results for 2 hours
pahebatcher config set cookie_string "cf_clearance=abc123; session=xyz"  # reuse browser cookies
pahebatcher config set auto_retry 2         # auto-retry failed episodes (0-2, 2 = 3 total attempts)
pahebatcher config reset                  # restore all defaults
```

Supported keys and their valid values:

| Key | Type | Default | Values |
|---|---|---|---|
| `quality` | integer | `1080` | `360`, `720`, `1080` |
| `audio_lang` | string | `jpn` | `jpn`, `eng` |
| `max_parallel` | integer | `2` | `1`–`6` |
| `hls_workers` | integer | `24` | `8`–`32` |
| `output_dir` | string | `.` | Any valid path |
| `keep_temp` | boolean | `false` | `true`, `false` |
| `resolve_ahead` | integer | `999` | `0`+ (how many episodes the resolver stays ahead of downloaders; set to `1` for serial resolution) |
| `cache_ttl` | integer | `60` | `0`+ (minutes before scan cache expires; `0` disables caching) |
| `cookie_string` | string | `""` | Semicolon-delimited cookies like `cf_clearance=abc123; session=xyz` |
| `auto_retry` | integer | `2` | `0`–`2` (0 = no retry, 2 = 3 total attempts per episode) |

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
| `--retry N` | — | Auto-retry failed episodes (0-2, 3 total) | `2` |
| `--verbose` | `-v` | Enable debug-level logging | off |

### Configuration commands

| Command | Description |
|---|---|
| `pahebatcher config show` | Display all settings with current values and defaults |
| `pahebatcher config set KEY VALUE` | Persist a setting (see Configuration section for key list) |
| `pahebatcher config reset` | Restore all settings to factory defaults |

### Watchlist commands (shorthand: `wl`, `w`, `watch` = `watchlist`; `check`/`sync` top-level)

| Command | Description |
|---|---|
| `pahebatcher watchlist add <URL> [-q Q] [--audio LANG] [-o DIR] [-j N] [-w N] [--keep-temp] [--retry N]` <br> `pahebatcher wl a <URL>` | Add anime to watchlist (mirrors download flags) |
| `pahebatcher watchlist check [--verbose] [URL|#]` <br> `pahebatcher check` / `pahebatcher wl c` / `pahebatcher sync` | Scan all watched anime for new episodes and download |
| `pahebatcher watchlist list` <br> `pahebatcher wl ls` / `wl l` | List watchlist entries |
| `pahebatcher watchlist show <URL|#>` <br> `pahebatcher wl s 1` | Show details for one entry |
| `pahebatcher watchlist remove <URL|#> [--yes]` <br> `pahebatcher wl rm 1` | Remove entry from watchlist |
| `pahebatcher watchlist reset <URL|#>` <br> `pahebatcher wl rst 1` | Clear deleted-history so deleted episodes will be re-downloaded |

### Watchlist specifics

- **Shorthand:** `watchlist` = `wl` = `w` = `watch`; top-level `check`/`sync` = `watchlist check`; sub-aliases `a`/`ls`/`l`/`s`/`rm`/`rst`/`c` (`main.py:121`). Examples: `pahebatcher wl add ...`, `pahebatcher check`, `pahebatcher wl ls`, `pahebatcher wl c`.
- **Idempotency & deleted skip:** `check` diffs `scan` vs. `output_dir` via `_find_existing` + `downloaded` history (`watchlist.py:30`). While `output_dir/sanitize(title)` exists, a once-downloaded but now-deleted episode shows `skipped (deleted)` and is not re-downloaded. Deleting the whole folder resets history (next `check` redownloads). `watchlist reset 1` clears history manually.
- **State file:** `watchlist.json` (JSON list of entries with `downloaded: [1,2]`). Back it up like `pahebatcher.toml`. Remove entries via `watchlist remove` or delete the file.
- **Make:** `make run ARGS="watchlist ..."` / `make run ARGS="wl c"` / `make run ARGS="check"` forward through the project venv (see `Makefile:42`); `make watchlist-list` / `make watchlist-check` are shortcuts.
- **Pipx/pip:** installed wheel includes `watchlist.py` (`pyproject.toml:43` `tool.setuptools.packages.find`), so `pahebatcher watchlist` / `wl` / `check` work identically with `make run`, `venv/bin/pahebatcher`, and `python -m pahebatcher`.

### Concurrency tuning

The two concurrency flags control different layers of parallelism:

- `-j` / `--parallel`: How many episodes download at once. Default 2. The resolver stage uses a single FlareSolverr instance, so values above 3-4 may not improve throughput. Increase if your internet connection has significant headroom.

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

The resolver runs ahead of downloaders via `asyncio.Queue` with `resolve_ahead + max_parallel` capacity (default 999 + max_parallel, effectively unlimited). Setting `resolve_ahead` to 1 limits the prefetch to a single episode, spreading the Kwik resolution requests across the download duration to avoid Cloudflare block triggers.

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

### Watchlist Check Pipeline

```
watchlist.json (downloaded: [1,2]) ──► for each entry:
  if not Path(output_dir/sanitize(title)).exists(): downloaded=[] (folder-deleted reset)
  AnimePaheScanner.scan(cache_ttl=0) ──► unique episodes by number (prefer audio) ──►
  BatchOrchestrator._find_existing() + downloaded history
    on_disk ? backfill history : (in history && folder_exists ? skip deleted : pending)
  ──► BatchOrchestrator.download(pending) (same 2-stage, SegmentStore resume)
  ──► update title/last_checked/downloaded ──► atomic save
  ──► summary table (New/Done/Failed; skipped (deleted) in dim)
```

Shared `Solver`/`HttpClient` (max `hls_workers` across entries). Failures are per-entry isolated.

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

FFmpeg concat demuxer: `ffmpeg -f concat -safe 0 -i concat.txt -c copy -movflags +faststart out.mp4`. Falls back to pipe mode (`cat segments | ffmpeg -i pipe:0`) if concat demuxer fails on malformed TS.

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
    config_manager.py        Persistent TOML config (validation, clamping, example)
    utils.py                 sanitize, ep_prefix, fmt_bytes, compact_ep_range
    tls.py                   Hardened SSL context (TLSv1.2+, forward secrecy)
    cache.py                 Async-safe TTLCache with asyncio.Lock and LRU eviction
    store.py                 SegmentStore (atomic writes, ffmpeg assembly, orphan cleanup)
    http.py                  HttpClient (shared aiohttp session, curl fallback, retry)
    solver.py                FlareSolverr client (instance-based, TTL cache, session rotation)
    extract/
        kwik.py              JsPacker, Kwik URL extraction, stream resolution
        m3u8.py              HLS manifest parser, AES-128 key extraction
        scanner.py           AnimePahe API scanner, URL validation, variant discovery
    downloader.py            EpisodeDownloader, BatchOrchestrator (2-stage pipeline)
    stream.py                MPV stream player with live panel and SUB/DUB navigation
    sessions.py              Session manager (list, resume, delete, clear)
    watchlist.py             Persistent watchlist (add/list/show/remove/check, JSON state, cron-safe)
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
make test        # run all 195 tests
make lint        # ruff check (0 errors)
make typecheck    # mypy strict (0 errors)
make benchmark   # full coherence benchmark: tests + lint + typecheck + coverage
make clean       # remove venv, caches, build artifacts
```

### Manual

```bash
pip install -e ".[dev]"
pytest tests/ -v              # 195 tests, asyncio auto-mode
pytest tests/ --cov=pahebatcher --cov-report=term  # with coverage
ruff check src/               # ALL rule select, target py311, 0 errors
mypy src/                     # strict mode, full type coverage, 0 errors
```

### Test structure

```
tests/
    conftest.py               Fixtures: mock solver, mock http, sample data
    test_cache.py             8 tests: set/get, eviction, expiry, concurrent access
    test_config_manager.py    10 tests: defaults, save/load, validation, clamping, existence
    test_config_extended.py   6 tests: constants, validation edge, roundtrip, corrupted TOML
    test_kwik.py              7 tests: JsPacker, M3U8 extraction, resolution buttons
    test_m3u8.py              8 tests: playlist parsing, AES keys, variant detection
    test_models.py            11 tests: dataclass fields, properties, edge cases (incl. cookie_str)
    test_prompts.py           11 tests: episode range parsing, noninteractive selection
    test_scanner.py           12 tests: URL validation, episode page parsing, search
    test_scanner_extended.py  7 tests: cache path, TTL expiry, glob-stable cache, scan fallback
    test_sessions.py          4 tests: cache listing, metadata, error handling
    test_solver.py            16 tests: cookie parsing, lifecycle, ping, cache, fetch_json/html
    test_http.py              8 tests: thread-local session, lifecycle, retry, 403 fallback, curl fetch
    test_tls.py               4 tests: TLS version, verify mode, ciphers, compression
    test_store.py             7 tests: segment I/O, atomic writes, assemble, cleanup
    test_store_extended.py    7 tests: atomic tmp handling, metadata, concat cleanup, pipe fallback
    test_downloader.py        7 tests: _find_existing, skip-existing, mocked download flow
    test_retry.py             4 tests: episode/retry (3 attempts), resolver retry, batch auto-retry
    test_ui.py                5 tests: episode/search/summary tables, dedup
    test_utils.py             20 tests: sanitize, ep_prefix, fmt_bytes, compact_ep_range
    test_utils_extended.py    21 tests: fmt_bytes precision, sanitize edge, ep_prefix, compact ranges
    test_coherence.py         7 tests: benchmark (ruff/mypy green), version, config coherence
                              ─────────────────────────────────────────────────────────
                              195 total
```

All tests run under `PYTHONPATH=src pytest tests/ -v` or with the package installed via `pip install -e .`.

### Code standards

- **Python 3.11+** with `from __future__ import annotations` throughout
- **mypy strict** -- no untyped defs, no implicit optionals, full type coverage (0 errors)
- **ruff ALL** -- pycodestyle + isort + pep8-naming + pyupgrade + bugbear + simplify + ruff-specific (0 errors)
- **Dependency injection** -- `AppContext` carries config through the call chain; `Solver`, `HttpClient` are constructor-injected
- **Zero global mutable state** -- no module-level caches, no classmethod singletons, no import-time side effects

### Benchmark

`make benchmark` (or `python scripts/benchmark.py`) runs the coherence suite:

```
ruff:       0 errors
mypy:       0 errors
pytest:     195 passed
coverage:   52% (1941 stmts, 938 missed — scrapers/downloader/stream require network/mocks)
loc:        3025 src, 1879 tests
density:    6.44 tests / 100 LOC
version:    3.3.0 coherent across pyproject.toml / config.py / __init__.py
```

Shared AES cache, atomic segment writes, and glob-stable scan cache are covered by the extended tests.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `FlareSolverr not responding` | FlareSolverr container not running | `docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr` |
| `No Kwik link found on episode page` | Site structure changed or Cloudflare blocking | Try a different quality value; ensure FlareSolverr has a healthy browser session. Alternatively set `cookie_string` with your browser's `cf_clearance` cookie |
| `Resolution timed out` | FlareSolverr overloaded or slow network | Retry with fewer concurrent episodes (`-j 1`); the resolver retries 5 times per episode |
| Segment download failures | CDN rate limiting | Lower `-w` to 8-12, lower `-j` to 1 |
| `MPV not found` | MPV not installed or not in PATH | Install via package manager or use Download mode instead |
| `ffmpeg` command not found | FFmpeg not installed | `sudo apt install ffmpeg` / `brew install ffmpeg` |
| `AES-128 stream detected` | pycryptodomex not installed (should not occur with `pip install`) | `pip install pycryptodomex` |
| `Playback ended` immediately in MPV | MPV HTTP/1.1 403 (CDN requires HTTP/2 headers) / `vo` missing | Fixed in 3.3.0 via local HLS proxy (`src/pahebatcher/stream.py:60`); if still, test `mpv --no-config --vo=null --ao=null --ytdl=no http://127.0.0.1:port/uwu.m3u8` and check `stderr` with `--verbose` |
| ARM64 (OrangepiZero3) `vo`/`hwdec` fail / slow software render | Mali/Panfrost not detected, `mpv` minimal build | `sudo apt install mpv ffmpeg ca-certificates`; try `mpv --hwdec=no --vo=gpu` or `--vo=drm --ao=alsa`; Armbian kernel recompile may be needed for GPU |
| Slow single-episode downloads | Low segment concurrency | Increase `-w` to 24-32; HLS segments are ~100 KB each and benefit from high parallelism |
| Cache directory growing too large | Old sessions accumulating | Use Session Manager (option 3 from main menu) to clear stale entries; >24h orphans are auto-cleaned |
| `watchlist.json` corrupted / `No watchlist entries` after edit | Hand-edited JSON invalid | Delete or `python -m json.tool watchlist.json` to validate; `load()` treats invalid as empty (`watchlist.py:76`) — re-add entries |
| `watchlist check` says `Up to date` but new episode expected | Scan cache stale or wrong `audio_lang` / `output_dir` | `check` forces `cache_ttl=0`; verify entry with `watchlist show 1` (audio/output); check `watchlist.json` output_dir matches where you look |
| `watchlist check` downloads nothing after move | Output files moved / renamed | `_find_existing` matches `Ep 001`/`Ep_001` prefix only (`downloader.py:154`); rename back or re-add entry |
| `watchlist add` updates instead of duplicates | Same `session` UUID | Intentional dedupe (`watchlist.py:134`); use `watchlist list` to see, `remove` first if you need a clean add |
| `watchlist` output in `/tmp` warns `volatile tmpfs` | `output_dir` `/tmp` is `tmpfs` cleared on reboot | Use persistent `./downloads` (default) or `~/anime`; otherwise `watchlist` folder-gone reset will redownload after reboot (`watchlist.py:305`) |
| `pahebatcher: error: unrecognized arguments: check` | Global `pahebatcher` stale (pipx 3.0.0) vs `venv` 3.3.0 with `watchlist` (`main.py:414`) | `make run` uses `venv` and works; for global use `venv/bin/pahebatcher watchlist check`, `venv/bin/python -m pahebatcher watchlist check`, or `make watchlist-check`, or refresh pipx: `pipx install . --force && hash -r` |

---

## Contributing

Bug reports, feature requests, and pull requests are welcome.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/description`)
3. Install dev dependencies: `pip install -e ".[dev]"`
4. Make changes; ensure `make lint` and `make typecheck` pass
5. Add or update tests; ensure `make test` passes (195 tests, asyncio auto-mode)
6. Commit with a conventional prefix (`fix:`, `feat:`, `refactor:`, `docs:`, `chore:`)
7. Push and open a pull request

Areas open to contribution:
- PyPI publication pipeline
- Graceful shutdown on Ctrl+C (finish active downloads before exit)
- Additional anime source support
- CI/CD with GitHub Actions
- Arrow-key navigable TUI menus

---

## License

MIT License. See [LICENSE](LICENSE) for full text.

This tool is intended for personal and educational use. Users are responsible for complying with the terms of service of the websites they access. Support official creators.
