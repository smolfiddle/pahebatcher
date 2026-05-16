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

MIT License. See [LICENSE](LICENSE) for details
