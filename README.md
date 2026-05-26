# pahebatcher — AnimePahe Batch Downloader

**pahebatcher** is a high‑performance TUI tool for [AnimePahe](https://animepahe.pw). It automates resolving, downloading, and streaming anime into a seamless, parallelized terminal experience.

Built with an asynchronous multi‑queue pipeline, a blazing‑fast HLS engine, and a persistent **Session Library** that remembers your progress.

![Screenshot 1](https://i.imgur.com/1Uc0hPo.png)
![Screenshot 2](https://i.imgur.com/RjKcvRq.png)

---

## 🚀 Quickstart

1. **Prerequisites:**
   - **[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)** – Must be running to bypass Cloudflare protection.
   - **[FFmpeg](https://ffmpeg.org/)** – Required for HLS segment remuxing.
   - **[MPV](https://mpv.io/)** – Optional, needed for streaming.

2. **Installation:**
   ```bash
   git clone https://github.com/smolfiddle/pahebatcher.git
   cd pahebatcher
   pip install -r requirements.txt
   ```

3. **Run:**
   ```bash
   python3 pahe_batcher.py "https://animepahe.pw/anime/<uuid>"
   ```

---

## ✨ Key Features (v2.3.0)

- **🚀 High-Speed Engine:**Decoupled prefetch pipeline resolves the next episode while the current one downloads. Parallel HLS segment fetching ensures maximum bandwidth saturation.
- **🔊 Robust Audio Detection:** Bypasses mislabelled API metadata by scanning play-page buttons directly. Guaranteed SUB/DUB accuracy for all series (including *Spy x Family*).
- **🎨 Unified TUI:** A polished, interactive wizard guides you through Quality, Audio, and Output settings for all modes (Download, Stream, and Export).
- **📂 Clean Naming:** Professional, standardized filenames (e.g., `Ep 001 - Title.mp4`) without messy internal markers.
- **💾 Session Management:** Dedicated library view to resume partial downloads or clean up cached segments. isolated cache folders per audio type prevent progress glitches.

---

## 📺 Action Modes

- **Batch Download:** Fetches segments concurrently and remuxes them into clean `.mp4` files.
- **Stream via MPV:** Watch anime directly with a live "Now Playing" panel and on-the-fly audio switching (`A` key).
- **Export Links:** Resolves M3U8 URLs and extracts authentication headers to a `links_export.txt` file.
- **List Mode:** Quickly view the episode table with sub/dub availability.

---

## 🛠 CLI Usage

```bash
python3 pahe_batcher.py <url> [options]
```

| Argument | Description | Default |
| :--- | :--- | :--- |
| `URL` | AnimePahe series URL. | **Optional** |
| `-a, --all` | Process every episode automatically. | `False` |
| `-r, --range` | Episode range (e.g., `1-12`, `1,4`, `13-`). | `None` |
| `-n, --latest`| Process the latest N episodes. | `None` |
| `-s, --stream`| Stream episodes via MPV. | `False` |
| `-e, --export`| Export M3U8 links and headers. | `False` |
| `-q, --quality`| Preferred quality: `1080`, `720`, or `360`. | `1080` |
| `-o, --output` | Output directory. | `./downloads`|
| `-j, --parallel`| Concurrent episode downloads (max 6). | `2` |
| `-w, --workers` | HLS workers per episode (max 32). | `24` |
| `--audio` | Audio preference: `jpn` (sub) or `eng` (dub). | `jpn` |

---

## ⚖️ Disclaimer

This tool is intended for personal and educational use. Support official creators.

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
