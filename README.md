# pahe-batcher — AnimePahe Batch Downloader

**pahe-batcher** is a high-performance, TUI-driven batch downloader and streamer for [AnimePahe](https://animepahe.ru). It features a parallel HLS segment engine, content-addressed deduplication, and direct integration with MPV for seamless streaming.

v1.1.0 introduces **Direct Streaming** and **Link Exporting**, making it more than just a downloader—it's a complete anime consumption toolkit.

![TUI Screenshot](https://i.imgur.com/example_tui.png)

---

## 🚀 Quickstart

1.  **Prerequisites:** 
    - Ensure **[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)** is running (required for Cloudflare bypass).
    - Install **[ffmpeg](https://ffmpeg.org/)** (for .ts → .mp4 remuxing).
    - Install **[mpv](https://mpv.io/)** (optional, for streaming).

2.  **Installation:**
    ```bash
    git clone https://github.com/smolfiddle/pahebatcher.git
    cd pahebatcher
    pip install -r requirements.txt
    ```

3.  **Run:**
    ```bash
    python3 pahe_batcher.py "https://animepahe.ru/anime/<uuid>"
    ```

---

## ✨ Features

### 📺 1. Action Modes
- **Download Locally:** Internal HLS engine fetches segments in parallel, deduplicates them (saving space on shared OPs/EDs), and remuxes them into high-quality `.mp4` files using FFmpeg.
- **Export Links:** Resolve all M3U8 links and authentication headers (User-Agent, Referer, Cookies) to a `links_export.txt` file. Perfect for use with IDM, JDownloader, or custom scripts.
- **Stream via MPV:** Watch anime directly from your terminal. Uses a live TUI dashboard and hardened header injection to bypass security checks in real-time.

### ⚙️ 2. Advanced Engine
- **Parallel HLS Engine:** Multi-threaded segment fetching (default 16 workers) for maximum speed.
- **SQLite Vault:** Uses a content-addressed chunk store to deduplicate identical HLS segments across different episodes.
- **Hardened TLS/SSL:** Uses a strict SSL context with modern ciphers and AEAD support for secure connections.
- **Adaptive Compression:** Entropy-based compression for stored HLS segments (saving up to 15% disk space during downloads).

### 🖥️ 3. Rich TUI & UX
- **Interactive Wizard:** A polished Step-by-Step setup for choosing episodes, quality, and concurrency.
- **Playback Controls:** While streaming, enjoy a navigation menu to skip episodes, replay, or jump to any item in your queue.
- **Full CLI Support:** Every feature is accessible via flags for automation and power users.

---

## 🛠 Usage & CLI

### Interactive Mode
Simply run the script with a series URL to start the interactive wizard:
```bash
python3 pahe_batcher.py https://animepahe.ru/anime/<uuid>
```

### Advanced CLI Flags
| Flag | Description |
| :--- | :--- |
| `-a, --all` | Download every episode automatically. |
| `-r, --range R` | Specify a range, e.g., `1-12`, `5,10`, or `13-`. |
| `-n, --latest N` | Download the latest N episodes. |
| `-e, --export` | Export links and headers to `links_export.txt`. |
| `-s, --stream` | Stream episodes directly via MPV. |
| `-q, --quality Q` | Preferred quality: `1080`, `720`, `360`. |
| `-o, --output DIR` | Set the output directory. |
| `-j, --parallel N` | Max concurrent downloads (default: 2). |
| `-y, --yes` | Skip all confirmation prompts. |

**Examples:**
- **Stream the last 3 episodes:**
  `python3 pahe_batcher.py <URL> --latest 3 --stream`
- **Export all links for an external manager:**
  `python3 pahe_batcher.py <URL> --all --export`
- **Download Season 1 in 720p:**
  `python3 pahe_batcher.py <URL> --range 1-12 --quality 720 --yes`

---

## 📦 Requirements

- **Python 3.8+**
- **FlareSolverr:** Required to handle Cloudflare challenges.
- **FFmpeg:** Required for merging HLS segments into MP4.
- **MPV:** Required only for the `--stream` feature.
- **Dependencies:** `rich`, `aiohttp`, `pycryptodomex` (for encrypted streams).

---

## 🧪 Testing

The project includes a comprehensive test suite covering database operations, HLS parsing, and async navigation logic.
```bash
pytest test_pahe_batcher.py
```

---

## ⚖️ Disclaimer

This tool is for educational purposes only. Please support the creators by watching on official platforms when possible. The authors are not responsible for any misuse of this tool.

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
