# pahe-batcher — AnimePahe Batch Downloader

**pahe-batcher** is a high-performance TUI tool for [AnimePahe](https://animepahe.pw). It automates the tedious process of resolving, downloading, and streaming anime into a seamless, parallelized terminal experience.

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

- **Batch Download:** Fetches HLS segments in parallel, stores them in a deduplicated SQLite vault, and remuxes them into organized `.mp4` files.
- **Export Links:** Resolves M3U8 URLs and extracts authentication headers (User-Agent, Referer, Cookies) to a `links_export.txt` file for use in IDM or JDownloader.
- **Stream via MPV:** Watch anime directly in your terminal with a live "Now Playing" dashboard and interactive playback controls (Next, Prev, Replay).

### ⚙️ High-Performance Engine

- **Parallel HLS Engine:** Uses `aiohttp` and `asyncio` to fetch segments with up to 32 concurrent workers.
- **SQLite Vault:** A content-addressed chunk store. If multiple episodes share the same segment (like a recap or OP), it's only stored once.
- **Adaptive Compression:** Entropy-based zlib compression for low-entropy segments (subtitles, silent audio) to save disk space during staging.
- **Hardened TLS:** Strict SSL/TLS 1.3 context with AEAD cipher selection for secure, modern connections.

### 🖥️ User Experience (TUI)

- **Interactive Wizard:** A beautiful, step-by-step TUI for configuring your batch without memorizing flags.
- **Live Dashboards:** Real-time progress tracking for downloads and a "Now Playing" console for streaming.
- **Smart Resume:** Interrupted downloads can be resumed seamlessly—the SQLite engine tracks exactly which segments are missing.

---

## 🏗️ Architecture Design

PaheBatcher follows a modular **Asynchronous Pipeline Architecture**.

### 1. The Scraper & Solver

- **AnimePahe API:** Scans series metadata and resolves the full episode list.
- **FlareSolverr Integration:** Automatically handles Cloudflare challenges and persists valid cookies across the session.
- **Kwik Resolver:** Unpacks obfuscated JavaScript to extract protected HLS manifests.

### 2. The Storage Layer (The Vault)

- **Content-Addressing:** Chunks are indexed by their BLAKE2b hash. This enables cross-episode deduplication.
- **WAL-Mode SQLite:** A high-concurrency database layer handles thousands of segment writes without locking.
- **Automatic Cleanup:** Temporary database assets are purged after successful remuxing unless `--keep-db` is specified.

### 3. The Delivery Layer

- **FFmpeg Remuxer:** Stitches segments into a stream-copy MP4 container with fast-start flags for immediate playback.
- **MPV Bridge:** Injects dynamic headers into the `lavf` demuxer to enable streaming of protected HLS feeds.

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

The project includes a full `pytest` suite covering the database, HLS parser, and async logic.

```bash
pytest test_pahe_batcher.py
```

---

## ⚖️ Disclaimer

This tool is intended for personal and educational use. Downloading copyrighted content may violate terms of service or local laws. Use responsibly.

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
