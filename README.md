# pahe-batcher — AnimePahe Batch Downloader

**pahe-batcher** is a professional-grade, high-performance TUI tool for [AnimePahe](https://animepahe.pw). It automates the tedious process of resolving, downloading, and streaming anime into a seamless, parallelized terminal experience.

Built with a modern asynchronous pipeline, it features a content-addressed SQLite segment engine that deduplicates shared data (like Openings/Endings) across episodes, saving significant disk space and bandwidth.

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
    pip install -r requirements.txt
    ```

3.  **Run:**
    ```bash
    python3 pahe_batcher.py "https://animepahe.pw/anime/<uuid>"
    ```

---

## ✨ Features

### 📺 Action Modes

- **Batch Download:** Fetches HLS segments in parallel, stores them in a deduplicated SQLite vault, and remuxes them into organized `.mp4` files using FFmpeg.
- **Export Links:** Resolves M3U8 URLs and extracts authentication headers (User-Agent, Referer, Cookies) to a `links_export.txt` file for use in IDM or JDownloader.
- **Stream via MPV:** Watch anime directly in your terminal with a live "Now Playing" dashboard and interactive playback controls (Next, Prev, Replay).

### ⚙️ High-Performance Engine

- **Parallel HLS Engine:** Uses `aiohttp` and `asyncio` to fetch segments with up to 32 concurrent workers per download.
- **SQLite Vault:** A content-addressed chunk store using BLAKE2b hashing. Identical HLS segments are stored only once, even if they appear in multiple episodes.
- **Adaptive Compression:** Entropy-based zlib compression for low-entropy segments (subtitles, silent audio) to save disk space during the staging process.
- **Hardened TLS:** Strict SSL/TLS 1.2+ context with AEAD cipher selection and certificate validation for secure, modern connections.

### 🖥️ User Experience (TUI)

- **Interactive Wizard:** A polished, step-by-step TUI for configuring your batch without memorizing flags.
- **Live Dashboards:** Real-time progress tracking for downloads and a "Live Playback" panel for streaming.
- **Smart Resume:** Interrupted downloads can be resumed seamlessly—the SQLite engine tracks exactly which segments are already persisted.

---

## 🏗️ Architecture Design

PaheBatcher follows a modular **Asynchronous Pipeline Architecture**.

### 1. The Scraper & Solver

- **AnimePahe API:** Scans series metadata and resolves the full episode list with JIT title fetching.
- **FlareSolverr Integration:** Automatically handles Cloudflare challenges and persists valid Chromium sessions across requests.
- **Kwik Resolver:** Unpacks obfuscated JavaScript to extract protected HLS manifests and authentication tokens.

### 2. The Storage Layer (The Vault)

- **Content-Addressing:** Chunks are indexed by their BLAKE2b hash. This enables seamless cross-episode deduplication for shared assets.
- **WAL-Mode SQLite:** A high-concurrency database layer using Write-Ahead Logging to handle thousands of segment writes without I/O blocking.
- **Automatic Cleanup:** Temporary database assets are purged after successful remuxing to keep your staging directory clean.

### 3. The Delivery Layer

- **FFmpeg Remuxer:** Stitches segments into a stream-copy MP4 container with `+faststart` flags for immediate playback across all devices.
- **MPV Bridge:** Injects dynamic headers directly into the `lavf` demuxer to enable streaming of protected HLS feeds with full decryption support.

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
| `-q, --quality`  | Preferred quality: `1080`, `720`, `360`.            | `1080`        |
| `-o, --output`   | Output directory.                                   | `./downloads` |
| `-j, --parallel` | Concurrent downloads (max 6).                       | `2`           |
| `-w, --workers`  | HLS segment workers per download (max 32).          | `16`          |
| `--keep-db`      | Do not delete the temp SQLite database.             | `False`       |
| `-y, --yes`      | Skip all confirmation prompts.                      | `False`       |

### Examples

**Stream the latest episode in 1080p:**

```bash
python3 pahe_batcher.py <URL> --latest 1 --stream
```

**Download a specific range with high concurrency:**

```bash
python3 pahe_batcher.py <URL> --range 1-12 --parallel 4 --workers 24
```

**Export links for an external manager:**

```bash
python3 pahe_batcher.py <URL> --all --export --output ~/Desktop/Links
```

---

## 🧪 Testing

The project includes a comprehensive `pytest` suite covering the database engine, HLS encryption, and async streaming logic.

```bash
pytest test_pahe_batcher.py
```

---

## ⚖️ Disclaimer

This tool is intended for personal and educational use. Downloading copyrighted content may violate terms of service or local laws. Support official creators.

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
