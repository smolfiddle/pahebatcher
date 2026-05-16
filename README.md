# pahebatcher — AnimePahe Batch Downloader

**pahebatcher** is a high‑performance TUI tool for [AnimePahe](https://animepahe.pw). It automates the tedious process of resolving, downloading, and streaming anime into a seamless, parallelized terminal experience.

Built with a modern asynchronous multi‑queue pipeline, a blazing‑fast HLS engine, and a persistent **Session Library** that remembers your progress.

![Screenshot 1](https://i.imgur.com/1Uc0hPo.png)
![Screenshot 2](https://i.imgur.com/RjKcvRq.png)

---

## 🚀 Quickstart

1. **Prerequisites:**
   - **[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)** – Must be running to bypass Cloudflare protection.
   - **[FFmpeg](https://ffmpeg.org/)** – Required for HLS segment remuxing (`.ts` → `.mp4`).
   - **[MPV](https://mpv.io/)** – Optional, needed for the `--stream` feature.

2. **Installation:**

   ```bash
   git clone https://github.com/smolfiddle/pahebatcher.git
   cd pahebatcher
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Run:**
   ```bash
   python3 pahe_batcher.py "https://animepahe.pw/anime/<uuid>"
   ```

---

## 📚 Usage Guide — Step by Step

### ① Launch the Wizard

Just pass an AnimePahe series URL (the one that looks like `https://animepahe.pw/anime/<uuid>`).
The tool checks that FlareSolverr is reachable, scans the series metadata, and shows an episode list with sub/dub counts.
If you’ve previously downloaded part of this series, you’ll see a `[PARTIAL DOWNLOAD FOUND]` badge and later the download will resume automatically.

### ② Choose an Action

After scanning, you’ll be greeted by the interactive action menu:

|  Key  | Action               | Description                                                                                     |
| :---: | :------------------- | :---------------------------------------------------------------------------------------------- |
| **1** | **Download**         | Batch‑download selected episodes as `.mp4` files.                                               |
| **2** | **Export**           | Save M3U8 links and authentication headers to a file for external downloaders.                  |
| **3** | **Stream**           | Watch episodes immediately via MPV with a live “Now Playing” panel.                             |
| **4** | **Sessions & Cache** | Browse, resume, or delete previous download sessions. Also cleans legacy `v1.x` database files. |
| **5** | **List**             | Display all episodes in a table (episode number, title, audio type).                            |
| **6** | **Exit**             | Quit the program.                                                                               |

_Pro tip:_ If you run the tool again on the same URL, your last‑used settings (quality, audio, output folder) are remembered and presented as defaults.

### ③ Select Episodes

Whether you pick **Download**, **Export**, or **Stream**, you’ll next select which episodes to process. Several modes are available:

- **A (All)** – Select every episode.
- **R (Range)** – Type a range like `1-12`, `1,4,7`, or `13-`.
- **L (Toggle)** – Interactive checklist; toggle individual episodes by number, then type `done`.
- **N (Latest N)** – Grab the most recent N episodes only.

The episode table shows sub (JPN) vs. dub (DUB) badges, so you always know what you’re selecting.

### ④ Configure Your Download/Stream Settings

An interactive wizard steps you through:

1. **Quality** – `360p`, `720p`, or `1080p` (default: `1080p`).
2. **Audio Language** – `Subbed` (Japanese original) or `Dubbed` (English). The tool auto‑detects and filters available streams on AnimePahe’s play page.
3. **Output directory** – Where your `.mp4` files will be saved (default: `./downloads/<series_name>`).
4. **Concurrency** – How many episodes to download in parallel (1‑6, default 2) and how many HLS segment workers per episode (8‑32, default 24).

Settings are reused when switching between download/export/stream actions during the same session, so you don’t have to re‑enter them.

### ⑤ Pre‑download Confirmation (Download Only)

Before the heavy lifting begins, you’ll see a summary panel that includes:

- Episode list and total count.
- Audio breakdown (e.g., `7 JPN, 0 DUB`).
- Estimated total size (based on a per‑episode quality estimate).
- Number of segments that will be reused from a previous partial download, if any.

Confirm with `y` (or `n` to go back and adjust selections) – and the downloads begin.

### ⑥ Monitoring Progress

The **Unified Dashboard** shows one row per episode, with columns for:

- Episode title.
- Progress bar (driven by segment count, so the percentage is accurate from the start).
- Segment counter (e.g., `150/200`).
- Real‑time transfer speed and ETA.
- Current file size.

Episodes transition through states: _resolving_ → _queued_ → _downloading_ → _remuxing_ → _done_. Failed episodes are clearly marked with a red `✗`.

### ⑦ Playback Controls (Stream Mode)

When streaming, after an episode finishes (close MPV), an interactive menu appears:

- **N** – Next episode.
- **P** – Previous episode.
- **R** – Replay current.
- **S** – Select a specific episode from a list.
- **Q** – Quit streaming.

### ⑧ Session & Cache Management (Action 4)

Opens a dedicated TUI that lists all previous download sessions stored in the `pahe_cache/` folder. For each session you can:

- **Resume** – Re‑scan the series and continue downloading unfinished episodes (completed segments are automatically skipped).
- **Delete** – Remove a session’s cached data to free disk space.
- **Clean Legacy** – Remove obsolete `.db` files from v1.x versions.

This turns pahebatcher into a persistent download manager, not just a one‑shot script.

---

## ✨ Features

### 📺 Action Modes

- **Batch Download** – Fetches HLS segments concurrently and remuxes them into well‑organized `.mp4` files using FFmpeg.
- **Export Links** – Resolves M3U8 URLs and extracts authentication headers to a `links_export.txt` file (compatible with IDM, JDownloader, etc.).
- **Stream via MPV** – Watch anime directly in the terminal with a live “Now Playing” dashboard and interactive playback controls.
- **Session & Cache Manager** – A dedicated library view to inspect, resume, or clean up active download sessions and free disk space.

### ⚙️ High‑Performance Engine

- **Prefetch Pipeline** – Resolver and download stages are decoupled; episode _N+1_ is resolved while episode _N_ downloads.
- **Direct Segment Store** – High‑speed file‑based staging (no SQLite overhead). Completed segments are tracked per episode, enabling seamless resume.
- **Shared Session Pool** – One tuned `aiohttp` connection pool (DNS cache, keep‑alive) serves all concurrent downloads for maximum throughput.
- **Hardened TLS** – Strict SSL/TLS 1.2+ context with AEAD ciphers for secure, modern connections.

### 🖥️ User Experience (TUI)

- **Unified Dashboard** – Segments‑based progress bars, real file sizes, transfer speeds, and ETA in a clean, column‑aligned layout.
- **Automatic Resume** – Partially downloaded series are detected at startup and displayed with a `[PARTIAL DOWNLOAD FOUND]` badge.
- **Pre‑download Summary** – Episode list, audio breakdown, estimated total size, and reused segment count before you confirm.
- **Reusable Settings** – Quality, audio language, and output directory are remembered when switching between download/export/stream modes.
- **Interactive Wizard** – A polished step‑by‑step TUI for configuring your batch settings without memorizing flags.

---

## 🏗️ Architecture

PaheBatcher follows a modular **asynchronous pipeline**.

1. **Resolver Stage** – Handles series metadata and stream extraction. Manages FlareSolverr sessions, solves JavaScript challenges, and resolves Kwik links into HLS manifests.

2. **Segment Store** – Staging area inside `pahe_cache/`, organized by anime title and episode. Segments are saved as numbered files, allowing persistent sessions that survive application restarts.

3. **Download Stage** – Multiple concurrent workers consume resolved stream info. Each worker uses a semaphore‑guarded sub‑pool of HLS fetchers to download segments at maximum speed while respecting CDN limits.

4. **Delivery Layer**
   - **FFmpeg Remuxer** – Concatenates segments into a stream‑copy MP4 container with `+faststart`.
   - **MPV Bridge** – Injects dynamic headers directly into the `lavf` demuxer for protected streaming.

---

## 🛠 CLI Usage

```bash
python3 pahe_batcher.py <url> [options]
```

| Argument         | Description                                   | Default       |
| :--------------- | :-------------------------------------------- | :------------ |
| `URL`            | AnimePahe series URL.                         | **Required**  |
| `-a, --all`      | Download every episode automatically.         | `False`       |
| `-r, --range`    | Episode range (e.g., `1-12`, `1,4`, `13-`).   | `None`        |
| `-n, --latest`   | Download the latest N episodes.               | `None`        |
| `-e, --export`   | Export M3U8 links and headers to a file.      | `False`       |
| `-s, --stream`   | Stream episodes via MPV.                      | `False`       |
| `-l, --list`     | List episodes and exit.                       | `False`       |
| `-q, --quality`  | Preferred quality: `1080`, `720`, or `360`.   | `1080`        |
| `-o, --output`   | Output directory.                             | `./downloads` |
| `-j, --parallel` | Concurrent episode downloads (max 6).         | `2`           |
| `-w, --workers`  | HLS segment workers per episode (max 32).     | `24`          |
| `--audio`        | Audio preference: `jpn` (sub) or `eng` (dub). | `jpn`         |
| `--keep-temp`    | Keep raw segment files after download.        | `False`       |

---

## 🧪 Testing

The project includes a `pytest` suite covering HLS parsing, CLI logic, and async streaming.

```bash
python3 -m pytest test_pahe_batcher.py
```

---

## ⚖️ Disclaimer

This tool is intended for personal and educational use. Downloading copyrighted content may violate terms of service or local laws. Support official creators.

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
