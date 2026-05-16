# pahe-batcher — AnimePahe Batch Downloader

**pahe-batcher** is a professional-grade, high-performance TUI tool for [AnimePahe](https://animepahe.pw). It automates the tedious process of resolving, downloading, and streaming anime into a seamless, parallelized terminal experience.

Built with a modern asynchronous multi-queue pipeline, it features a high-speed HLS engine and a persistent "Session Library" that ensures your progress is never lost.

![Screenshot 1](https://i.imgur.com/1Uc0hPo.png)
![Screenshot 2](https://i.imgur.com/RjKcvRq.png)

---

## 🚀 Quickstart

1.  **Prerequisites:**
    - **[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)**: Must be running to bypass Cloudflare protection.
    - **[FFmpeg](https://ffmpeg.org/)**: Required for HLS segment remuxing (.ts → .mp4).
    - **[MPV](https://mpv.io/)**: Optional, required for the `--stream` feature.

2.  **Installation:**

    ```bash
    git clone https://github.com/smolfiddle/pahebatcher.git
    cd pahebatcher
    python3 -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate
    pip install -r requirements.txt
    ```

3.  **Run:**
    ```bash
    python3 pahe_batcher.py "https://animepahe.pw/anime/<uuid>"
    ```

---

## ✨ Features

### 📺 Action Modes

- **Batch Download:** Fetches HLS segments in parallel and remuxes them into organized `.mp4` files using FFmpeg.
- **Export Links:** Resolves M3U8 URLs and extracts authentication headers to a `links_export.txt` file for use in IDM or JDownloader.
- **Stream via MPV:** Watch anime directly in terminal with a live "Now Playing" dashboard and interactive playback controls.
- **Session & Cache Manager:** A dedicated library view to manage, resume, or cleanup active download sessions and disk usage.

### ⚙️ High-Performance Engine

- **Multi-Queue Pipeline:** Decoupled resolver and download stages. Workers start downloading segments the moment the first stream is resolved.
- **Direct Segment Store:** High-speed file-based staging engine (no SQLite overhead). Supports seamless resume by tracking existing segments.
- **Shared Session Pool:** Tuned `aiohttp` connection pool with DNS caching and keep-alive for massive throughput.
- **Hardened TLS:** Strict SSL/TLS 1.2+ context with AEAD cipher selection for secure, modern connections.

### 🖥️ User Experience (TUI)

- **Standardized Dashboard**: Unified progress tracking with column headers, accurate file sizes, and segment-based ETA.
- **Contextual Resume**: Automatically detects partial downloads during scanning and displays a `[PARTIAL DOWNLOAD FOUND]` badge.
- **Interactive Wizard:** A polished, step-by-step TUI for configuring your batch settings without memorizing flags.

---

## 🏗️ Architecture Design

PaheBatcher follows a modular **Asynchronous Pipeline Architecture**.

### 1. The Scraper & Resolver Stage
A dedicated resolver process handles series metadata and stream extraction. It manages FlareSolverr sessions and JavaScript unpacking to provide valid HLS manifests to the downloader workers.

### 2. The Segment Store (Staging)
Located in `pahe_cache/`, this layer manages the persistence of individual HLS segments. It is organized by anime title and episode, allowing for persistent sessions that survive application restarts.

### 3. The Download Stage
Multiple concurrent workers consume the resolved stream info. Each worker uses a semaphore-guarded sub-pool of HLS workers to fetch segments at maximum speed while preventing CDN rate-limiting.

### 4. The Delivery Layer
- **FFmpeg Remuxer:** Stitches segments into a stream-copy MP4 container with `+faststart` flags.
- **MPV Bridge:** Injects dynamic headers directly into the `lavf` demuxer to enable protected streaming.

---

## 🛠 Usage & CLI Arguments

### CLI Flags

```bash
python3 pahe_batcher.py <url> [options]
```

| Argument         | Description                                         | Default       |
| :--------------- | :-------------------------------------------------- | :------------ |
| `URL`            | AnimePahe series URL.                               | **Required**  |
| `-a, --all`      | Download every episode automatically.               | `False`       |
| `-r, --range`    | Specify episode range (e.g., `1-12`, `1,4`, `13-`). | `None`        |
| `-n, --latest`   | Download the latest N episodes.                     | `None`        |
| `-e, --export`   | Export links/headers to a file.                     | `False`       |
| `-s, --stream`   | Stream episodes via MPV.                            | `False`       |
| `-l, --list`     | List episodes and exit.                             | `False`       |
| `-q, --quality`  | Preferred quality: `1080`, `720`, `360`.            | `1080`        |
| `-o, --output`   | Output directory.                                   | `./downloads` |
| `-j, --parallel` | Concurrent episode downloads (max 6).               | `2`           |
| `-w, --workers`  | HLS segment workers per episode (max 32).           | `24`          |
| `--audio`        | Audio preference: `jpn` (sub) or `eng` (dub).       | `jpn`         |
| `--keep-temp`    | Keep raw segment files after download.              | `False`       |

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
