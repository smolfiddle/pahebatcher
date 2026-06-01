# pahebatcher — AnimePahe Batch Downloader

**pahebatcher** is a high-performance terminal tool for [AnimePahe](https://animepahe.pw). It automates discovering, downloading, streaming, and exporting anime series into a seamless, parallelized TUI experience.

Built around an asynchronous two-stage prefetch pipeline with a multi-worker HLS engine and a persistent segment cache that survives crashes.

![Screenshot 1](https://i.imgur.com/1Uc0hPo.png)
![Screenshot 2](https://i.imgur.com/RjKcvRq.png)

---

## Prerequisites

| Requirement | Purpose | Install |
|---|---|---|
| **[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)** | Cloudflare bypass | `docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr` |
| **[FFmpeg](https://ffmpeg.org/)** | TS → MP4 remux | `sudo apt install ffmpeg` / `brew install ffmpeg` |
| **[MPV](https://mpv.io/)** (optional) | Streaming mode | `sudo apt install mpv` / `brew install mpv` |
| **Python 3.8+** | Runtime | `python3 --version` |

The tool checks FlareSolverr reachability on startup (`FLARESOLVERR_URL` env var, default `http://localhost:8191/v1`). If unreachable, it prints the exact Docker command and exits.

---

## Installation

```bash
git clone https://github.com/smolfiddle/pahebatcher.git
cd pahebatcher
pip install -r requirements.txt
```

### Python dependencies

| Package | Version | What it does |
|---|---|---|
| `rich` | ≥13.0 | TUI: progress bars, panels, tables, prompts, live-updating dashboards |
| `aiohttp` | ≥3.8 | Async HTTP — **3–5× faster segment downloads** vs urllib. Falls back gracefully if missing. |
| `pycryptodomex` | ≥3.15 | AES-128-CBC decryption for DRM-protected HLS streams |
| `pytest` | ≥7.0 | Test runner (13 unit tests covering URL parsing, JS unpacking, M3U8 parsing, audio detection) |
| `pytest-asyncio` | ≥0.21 | Async test support |

---

## Step-by-Step: Batch Download an Anime Series

### Step 1 — Find your series

Open your browser, navigate to [animepahe.pw](https://animepahe.pw) (or `.com` / `.org` / `.ru`), and copy the URL of the series page. It should look like:

```
https://animepahe.ru/anime/540562da-0708-2e0f-2178-01306c59b207
```

Alternatively, run the tool with no URL and use the built-in interactive search:

```bash
python3 pahe_batcher.py
```

### Step 2 — Run the tool

```bash
python3 pahe_batcher.py "https://animepahe.ru/anime/<uuid>"
```

The tool scans the series, fetches all sub/dub variants, and shows you the action menu:

```
 1  Download · save .mp4 files
 2  Export   · get M3U8 URLs + headers
 3  Stream   · play in MPV
 4  Sessions & Cache
 5  List     · show episode table
 6  Exit
```

Select **1 (Download)**.

### Step 3 — Choose quality (recommended: 720p)

| Quality | ~Size per episode | When to use |
|---|---|---|
| **720p** (recommended) | ~90 MB | Good size/quality balance. Fastest downloads. |
| 1080p | ~150 MB | Best visual quality. Heavier on disk & bandwidth. |
| 360p | ~50 MB | Low bandwidth / preview only. |

If a requested resolution isn't available for a specific episode, the tool auto-selects the closest lower resolution.

### Step 4 — Choose audio (recommended: SUB / Japanese)

- **SUB** (`jpn`) — Original Japanese audio. Widest availability.
- **DUB** (`eng`) — English dub. Not all series have it; the tool falls back to SUB automatically when DUB is missing.

Audio detection uses three strategies in sequence: CLI preference → DOM `data-audio` attribute → button class names. This bypasses mislabelled API metadata on series where the site incorrectly tags episodes.

### Step 5 — Set output directory

Default: `./downloads/<Sanitized_Title>/`

The tool auto-creates the directory. Each episode is saved as:

```
Ep 001 - Clean Title.mp4
Ep 005.5 - OVA Title.mp4
```

### Step 6 — Set concurrency (recommended: defaults)

| Setting | Default | Recommended | What it controls |
|---|---|---|---|
| Concurrent downloads (`-j`) | 2 | 2 | How many episodes download at once. Range 1–6. |
| HLS workers per episode (`-w`) | 24 | 24 | Parallel segment fetches **within** one episode. Range 8–32. |

**Why these defaults work:**
- `-j 2`: The resolver stage is serial (one FlareSolverr browser at a time), so 2 downloaders is the sweet spot — one episode resolves while another downloads, but you rarely saturate beyond 3.
- `-w 24`: HLS segments are small (~100 KB). 24 concurrent TCP connections saturate most residential connections without triggering CDN rate limits.

If you see failed segments, lower `-w` to 8–12.

### Step 7 — Select episodes

```
 Select mode
 ╭────────────────────────────────────╮
 │  A  All episodes                   │
 │  R  Range    e.g. 1-12  or  1,4,7  │
 │  L  Toggle   interactive checklist │
 │  N  Latest N                       │
 │  S  Skip                           │
 ╰────────────────────────────────────╯
```

- **A** — download everything
- **R** — enter a range like `1-12`, `1,4,7`, `13-` (open-ended)
- **L** — toggle individual episodes with an interactive checklist table
- **N** — grab only the most recent N episodes (e.g. `3`)

### Step 8 — Confirm and download

A summary panel shows series name, episode count, audio/sub choice, estimated disk usage, and how many segments are reusable from prior sessions.

```
 ╭───────────────────────────────────────────╮
 │  Series:    Spy x Family                  │
 │  Episodes:  12  (1–12)                    │
 │  Audio:     SUB                           │
 │  Quality:   720p                           │
 │  Output:    ./downloads/Spy_x_Family      │
 │  Reusing:   847 segments from previous session  │
 │  Est. size: ~1080 MB  (~90 MB/ep × 12 eps)     │
 ╰───────────────────────────────────────────╯
 Start download? [y/n]
```

The live dashboard shows per-episode progress with segment counts, transfer speed, ETA, and file sizes.

### All-in-one CLI version (non-interactive)

```bash
# Download all episodes, 720p SUB, 2 parallel
python3 pahe_batcher.py "URL" --all -q 720 -j 2

# Download episodes 1-12, 1080p DUB
python3 pahe_batcher.py "URL" --range 1-12 -q 1080 --audio eng

# Stream latest 3 episodes in MPV
python3 pahe_batcher.py "URL" --latest 3 --stream
```

---

## Architecture

### Two-stage prefetch pipeline

```
  ┌──────────────────────────────────────────────────────────┐
  │  Stage 1 — Resolver (serial, 1 worker)                   │
  │  FlareSolverr fetches episode play pages, extracts Kwik  │
  │  URLs, and resolves them to M3U8 manifests. One at a     │
  │  time because FlareSolverr uses a single Chromium tab.   │
  ├──────────────────────────────────────────────────────────┤
  │  Stage 2 — Downloaders (concurrent, N workers)           │
  │  Each downloader fetches HLS segments in parallel (24    │
  │  workers per episode), decrypts AES-128 if needed, and   │
  │  writes segments atomically to the segment store.        │
  └──────────────────────────────────────────────────────────┘
```

The resolver runs slightly ahead of downloaders via an `asyncio.Queue` with capacity `max_parallel + 2`. This ensures no idle time — the next episode is always ready when a downloader finishes.

### Shared aiohttp session

A single `aiohttp.ClientSession` is created for the entire batch, with:

- `limit=0` (no global connection cap — semaphores control concurrency)
- `limit_per_host=hls_workers` — bounds per-CDN connections
- `keepalive_timeout=45s` — TCP connection reuse across segments of the same stream
- 5-minute DNS cache
- Hardened TLS: TLSv1.2 minimum, forward-secrecy cipher list only, CERT_REQUIRED

### Segment Store

```
pahe_cache/
  Spy_x_Family_<uuid>/
    session.json          ← metadata (title, URL, timestamp)
    Ep_001_JPN/
      000000.ts
      000001.ts
      ...
      concat.txt          ← ffmpeg concat demuxer playlist
    Ep_002_JPN/
      ...
```

- **Atomic writes**: segments are written to `.tmp` files then renamed to `.ts`, preventing partial files on crash/interrupt.
- **Resume-safe**: on restart, the tool reads `done_indices()` and only fetches missing segments. Previously downloaded episodes in the output directory are skipped entirely.
- **Orphan cleanup**: cache files older than 24 hours without a corresponding active session are auto-cleaned at exit.

### Kwik → M3U8 resolution chain

1. Fetch play page via FlareSolverr
2. Parse resolution menu buttons (`data-src`, `data-resolution`, `data-audio`, `data-fansub`)
3. Fetch Kwik page directly with `Referer: https://animepahe.com/` (bypasses Cloudflare without FlareSolverr)
4. Extract M3U8 URL using three strategies:
   - Direct regex match in HTML
   - JS deobfuscation (eval-wrapped + `JsPacker` unpacker for `p,a,c,k,e,d` packed scripts)
   - `<source>` tag fallback
5. Parse M3U8: follow `#EXT-X-STREAM-INF` variants (last = highest quality), handle `#EXT-X-KEY` for AES-128
6. Fetch AES key (cached with 1-hour TTL)
7. Download segments, decrypt, assemble

### Muxing

Attempted in order:
1. **FFmpeg concat demuxer** — single-pass, fastest: `ffmpeg -f concat -i concat.txt -c copy -movflags +faststart out.mp4`
2. **FFmpeg pipe** — cat all segments → stdin → ffmpeg (fallback for broken concat demuxer)
3. **Raw TS concatenation** — saved as `.ts` if ffmpeg is unavailable or fails

### Retry policy

5 attempts per request with exponential backoff: `0.5s × 2^attempt`. Applied to segment fetches, Kwik resolution, and FlareSolverr API calls. Session-auto-recreation on FlareSolverr "session not found" errors.

---

## CLI Reference

```bash
python3 pahe_batcher.py <URL> [options]
```

| Flag | Description | Default |
|---|---|---|
| `URL` | AnimePahe series URL. Optional — launches interactive search if omitted. | — |
| `-a`, `--all` | Download/sync every episode. | — |
| `-r`, `--range RANGE` | Episodes to process: `1-12`, `1,4,7`, `13-`. | — |
| `-n`, `--latest N` | Process the latest N episodes. | — |
| `-l`, `--list` | Scan series and print episode table, then exit. | — |
| `-e`, `--export` | Resolve M3U8 URLs and write `links_export.txt` with headers + ready ffmpeg commands. | — |
| `-s`, `--stream` | Stream episodes via MPV with live playback panel and audio switching. | — |
| `-q`, `--quality Q` | Preferred resolution: `360`, `720`, `1080`. | `1080` |
| `-o`, `--output DIR` | Output directory. Series name is appended as a subdirectory. | `./downloads` |
| `-j`, `--parallel N` | Concurrent episode downloads (1–6). | `2` |
| `-w`, `--workers N` | HLS segment fetchers per episode (8–32). | `24` |
| `--audio LANG` | Audio preference: `jpn` (subbed) or `eng` (dubbed). | `jpn` |
| `--keep-temp` | Keep raw `.ts` segment files after muxing (debugging). | off |

### Examples

```bash
# Interactive wizard (search or paste URL)
python3 pahe_batcher.py

# Full batch: all episodes, 720p, subbed, 2 concurrent
python3 pahe_batcher.py "URL" --all -q 720 -j 2

# Season 1, dubbed, 1080p, custom output
python3 pahe_batcher.py "URL" --range 1-12 --audio eng -q 1080 -o ~/anime

# Just list episodes with audio availability
python3 pahe_batcher.py "URL" --list

# Stream in MPV with on-the-fly SUB↔DUB switching
python3 pahe_batcher.py "URL" --stream -q 1080

# Export M3U8 URLs to a file
python3 pahe_batcher.py "URL" --all --export
```

---

## Action Modes

### Download
Fetches, decrypts, and remuxes HLS streams into `.mp4` files. Uses the shared aiohttp session and segment store for resume safety. Clean filenames: `Ep 001 - Title.mp4`.

### Stream (MPV)
Launches MPV with the resolved M3U8 URL and authentication headers. Provides a live "Now Playing" panel and post-playback navigation:
- **N**ext / **P**rev — navigate playlist
- **A**udio — toggle SUB↔DUB on the fly
- **R**eplay — restart current episode
- **S**elect — jump to any episode by number

MPV is invoked with `--demuxer-lavf-format=hls` and cookies/referer passed via both CLI flags and demuxer options for maximum compatibility.

### Export
Resolves all episode M3U8 URLs and writes `links_export.txt` to the output directory. Each entry includes:
- M3U8 URL
- User-Agent, Referer, Cookie headers
- A ready-to-run `ffmpeg` command

Useful for downloading on a different machine or with external tools.

### List
Scans the series and prints the episode table (number, title, audio availability) without downloading anything.

---

## Session Manager

Accessible from the main menu (option 4). Lists all cached sessions with:

- Anime title
- Episode count / segment count
- Cache size on disk
- Status (Paused / Obsolete)

Actions:
- **Resume** — restart the tool with that series URL
- **Delete** — remove a single session's cache
- **Clear All** — wipe the entire `pahe_cache/` directory
- **Legacy Cleanup** — removes old `.db`/`.pahe_staging_*` files from v1.x

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `FlareSolverr not responding` | FlareSolverr not running | `docker run -d --name=flaresolverr -p 8191:8191 ghcr.io/flaresolverr/flaresolverr` |
| `aiohttp not installed` | Missing optional dependency | `pip install aiohttp` (tool falls back to slower urllib) |
| `AES-128 stream detected` | HLS stream has DRM | `pip install pycryptodomex` |
| `ffmpeg not in PATH` | FFmpeg missing | Install ffmpeg; tool falls back to raw `.ts` output |
| Segments failing / slow | CDN rate limiting | Lower `-w` to 8–12, lower `-j` to 1 |
| `MPV not found` | MPV not installed | Install mpv or use Download mode instead |
| `No Kwik link found` | Site structure changed | Try a different quality or report the series |
| `Resolution timed out` | FlareSolverr overloaded | Wait and retry; the resolver retries 5 times per episode |

---

## Testing

```bash
pytest test_pahe_batcher.py -v
```

13 tests covering: URL parsing, JS unpacker, episode range parsing, TTL cache eviction, M3U8 parsing (AES-128, variant selection), resolution button parsing, stream extraction logic, scanner pipeline, filename sanitization, episode prefix formatting, and stream navigation.

---

## Disclaimer

This tool is intended for personal and educational use. Support official creators.

## License

MIT License. See [LICENSE](LICENSE) for details.
