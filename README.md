# Pahe-Batcher 🚀

### _Parallel HLS Acquisition & Batch Processing Engine_

**Pahe-Batcher** is a high-performance, asynchronous automation tool designed for the precision acquisition of media from AnimePahe. Engineered with a focus on speed, storage efficiency, and architectural robustness, it leverages a sophisticated **Content-Addressable Storage (CAS)** system to provide a seamless batch-downloading experience.

---

## 🏛 Architectural Overview

### 💎 Smart Content Deduplication (CAS)

Utilizing a **BLAKE2b content-addressed chunk store**, Pahe-Batcher identifies identical HLS segments (such as standardized Openings, Endings, or Eye-catches) across multiple episodes. These segments are stored **exactly once** in the staging database, significantly reducing the temporary disk footprint for large series.

### ⚡ Hybrid Async Execution Engine

The core engine is built on a high-concurrency `asyncio` loop, managing:

- **Massive Parallelism:** Orchestrates up to 32 concurrent HLS segment workers per episode.
- **Concurrent Batching:** User-configurable limits (1-6) for simultaneous episode downloads.
- **Adaptive IO:** A thread-safe SQLite connection pool utilizing **WAL (Write-Ahead Logging)** mode for non-blocking database interactions.

### 🧠 Adaptive Entropy-Based Compression

Before storage, every data chunk undergoes real-time Shannon entropy analysis:

- **High-Entropy Data:** Compressed video/audio streams are stored raw to preserve CPU cycles.
- **Low-Entropy Data:** Silent frames, subtitle segments, or empty audio are automatically **zlib-compressed**, maximizing storage density.

### 🛡️ Resilience & Security

- **FlareSolverr Integration:** Intelligent browser session management bypasses Cloudflare challenges without leaking resources.
- **Recursive JS Unpacking:** Built-in `JsPacker` handles multi-layered `eval` obfuscation common in Kwik/AnimePahe players.
- **Safe Exporting:** Automatic sanitization of titles ensures compatibility across all file systems (Linux, Windows, macOS).

---

## ✨ Features

- **Professional TUI:** Real-time dashboard powered by `Rich` with multi-tier progress bars, transfer speeds, and time estimations.
- **Automatic Organization:** Episodes are automatically sorted into sanitized sub-folders named after the series.
- **Efficient Staging:** The database acts as a temporary staging area with `PRAGMA auto_vacuum = FULL` to reclaim space immediately after export.
- **FFmpeg Integration:** Seamless HLS-to-MP4 remuxing with fallback to `.ts` if FFmpeg is unavailable.
- **Granular Selection:** Support for "All", "Latest N", or custom range selection (e.g., `1-12, 15, 20-`).

---

## 🛠 Prerequisites

- **Python:** 3.10+
- **FlareSolverr:** Must be running (Default: `http://localhost:8191/v1`).
- **FFmpeg:** Installed in your system `PATH` (optional, for MP4 remuxing).

---

## 📥 Installation

1. **Clone the repository:**

   ```bash
   git clone https://github.com/your-repo/pahe-batcher.git
   cd pahe-batcher
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

---

## 📖 Usage Guide

### 1. Interactive Wizard (Recommended)

Simply provide the series URL to enter the interactive selection mode:

```bash
python pahe_batcher.py "https://animepahe.pw/anime/<uuid>"
```

### 2. Automated Batching (Non-Interactive)

| Command        | Description                                               |
| :------------- | :-------------------------------------------------------- |
| `--all`        | Download every episode in the series immediately.         |
| `--range 1-12` | Download a specific range of episodes.                    |
| `--latest 5`   | Download the 5 most recently aired episodes.              |
| `--list`       | Output the episode list and metadata without downloading. |

### 3. Advanced Configuration Flags

| Flag               | Default       | Description                                       |
| :----------------- | :------------ | :------------------------------------------------ |
| `-o`, `--output`   | `./downloads` | Root directory for downloads.                     |
| `-j`, `--parallel` | `2`           | Number of simultaneous episode downloads (max 6). |
| `-w`, `--workers`  | `16`          | HLS segment workers per download (max 32).        |
| `--keep-db`        | `False`       | Retain the staging database after download.       |
| `-y`, `--yes`      | `False`       | Skip all confirmation prompts.                    |

---

## ⚙️ Workflow Logic

1.  **Scan:** Tool retrieves the series index via FlareSolverr.
2.  **Selection:** User defines the target queue.
3.  **Extraction:** Kwik links are resolved and unpacked.
4.  **Acquisition:** HLS segments are pulled in parallel and hashed into the DB.
5.  **Export:** Segments are joined, remuxed via FFmpeg, and moved to the series folder.
6.  **Cleanup:** Staging chunks are purged and the database is vacuumed or deleted.

---

## ⚖️ Disclaimer

This software is intended for personal archival and educational purposes only. Users are responsible for ensuring their use of the tool complies with local laws and the terms of service of the content providers.

---

**Version:** 1.0.0
**License:** MIT
**Maintainer:** Gemini CLI Refactor Project
