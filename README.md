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
| **1** | **Download**         | Batch‑download selected episodes as clean `.mp4` files.                                         |
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

The episode table allows you to see the available content, and the tool will automatically handle audio track selection based on your preferences.

### ④ Configure Your Settings (The Unified Wizard)

A polished, unified wizard guides you through the configuration for all modes:

1. **Quality** – `360p`, `720p`, or `1080p` (default: `1080p`).
2. **Audio Language** – `Subbed` (Japanese original) or `Dubbed` (English).
   - **Robust Detection (v2.3.0+):** The tool now ignores potentially incorrect API metadata and scans the actual play page buttons to ensure you get the exact audio track you requested.
3. **Output directory** (Download only) – Where your clean `.mp4` files will be saved.
4. **Concurrency** (Download only) – Parallel download settings for maximum throughput.

### ⑤ Pre‑download Confirmation (Download Only)

Before the heavy lifting begins, you’ll see a summary panel that includes:

- Episode list and total count.
- Audio preference (SUB or DUB).
- Estimated total size (based on a per‑episode quality estimate).
- Number of segments that will be reused from a previous partial download, if any.

Confirm with `y` – and the downloads begin.

### ⑥ Monitoring Progress

The **Unified Dashboard** shows one row per episode, with columns for:

- Episode title (cleaned of redundant audio suffixes).
- Progress bar (driven by segment count).
- Real‑time transfer speed, ETA, and file size.

Episodes transition through states: _resolving_ → _queued_ → _downloading_ → _remuxing_ → _done_.

### ⑦ Playback Controls (Stream Mode)

When streaming, after an episode finishes (close MPV), an interactive menu appears:

- **N** – Next episode.
- **P** – Previous episode.
- **A** – Switch audio (SUB/DUB) on the fly.
- **R** – Replay current.
- **S** – Select a specific episode from a list.
- **Q** – Quit streaming.

### ⑧ Session & Cache Management (Action 4)

Opens a dedicated TUI that lists all previous download sessions. **v2.3.0+** uses isolated cache folders per audio type (e.g., `Ep_1_JPN`), ensuring that downloading different audio tracks for the same episode doesn't cause conflicts or progress glitches.

---

## ✨ Features

### 📺 Action Modes

- **Batch Download** – Fetches HLS segments concurrently and remuxes them into clean, standardized `.mp4` files without messy filename suffixes.
- **Export Links** – Resolves M3U8 URLs and extracts authentication headers to a `links_export.txt` file.
- **Stream via MPV** – Watch anime directly with a live “Now Playing” dashboard and robust audio track switching.
- **Session & Cache Manager** – Dedicated library view to inspect, resume, or clean up download sessions.

### ⚙️ High‑Performance Engine

- **Robust Audio Discovery** – Bypasses mislabelled API metadata by directly scanning website buttons for `eng`/`jpn` markers.
- **Prefetch Pipeline** – Resolver and download stages are decoupled; episode _N+1_ is resolved while episode _N_ downloads.
- **Isolated Segment Store** – Audio-specific cache directories prevent progress collisions between Sub and Dub tracks.
- **Shared Session Pool** – One tuned `aiohttp` connection pool for maximum throughput.

### 🖥️ User Experience (TUI)

- **Unified Configuration** – The same polished wizard interface for all operational modes.
- **Clean Naming** – Professional, standardized filenames (e.g., `Ep 001 - Title.mp4`) without redundant `_SUB`/`_DUB` markers.
- **Automatic Resume** – Reliable detection and resumption of partial downloads based on exact audio tracks.

---

## 🏗️ Architecture

PaheBatcher follows a modular **asynchronous pipeline**.

1. **Resolver Stage** – Handles metadata and robust stream extraction. Now features play-page button analysis to guarantee audio track accuracy.

2. **Segment Store** – Organizes segments by anime title, episode, and **audio type** inside `pahe_cache/`, ensuring persistent, conflict-free sessions.

3. **Download Stage** – Multiple concurrent workers utilize a semaphore‑guarded sub‑pool of HLS fetchers for maximum speed.

4. **Delivery Layer**
   - **FFmpeg Remuxer** – High-speed stream-copy to MP4 with clean naming.
   - **MPV Bridge** – Instant-start playback via direct header injection into the `lavf` demuxer.

---

## 🛠 CLI Usage

```bash
python3 pahe_batcher.py <url> [options]
```

| Argument         | Description                                   | Default       |
| :--------------- | :-------------------------------------------- | :------------ |
| `URL`            | AnimePahe series URL.                         | **Optional**  |
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
