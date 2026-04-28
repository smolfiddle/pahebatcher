# PaheBatcher

PaheBatcher is a zero-hassle CLI batch downloader for [AnimePahe](https://animepahe.ru). It automates episode resolution and parallel downloading — turning a tedious multi-click process into a single terminal command.

Built with a modular pipeline architecture: an API scraper feeds into a link resolver, which feeds into a concurrent download manager — all driven via pure CLI flags for quick staging, requiring just an AnimePahe URL to get started.

![First screenshot](https://i.imgur.com/pahebatcher_demo1.png)
![Second screenshot](https://i.imgur.com/pahebatcher_demo2.png)

---

## Quickstart

1.  Ensure [FlareSolverr](https://github.com/FlareSolverr/FlareSolverr) is running (required to bypass Cloudflare protection).
2.  Install the required packages:

```bash
pip install -r requirements.txt
```

3.  Run the tool with an AnimePahe anime URL:

```bash
python3 pahebatcher.py "https://animepahe.ru/a/1234"
```

4.  Downloads begin automatically based on your CLI arguments.

---

## Features

### Direct Staging & Discovery

- **URL-Driven Staging:** Pass an AnimePahe anime URL directly for immediate processing — no interactive prompts, perfect for quick staging and scripting.
- **Episode Listing:** Fetches and displays all available episodes with dub/sub indicators before downloading.
- **Cloudflare Bypass:** Integrates seamlessly with FlareSolverr to handle AnimePahe's anti-bot protection automatically.

### Download Engine

- **Batch Downloading:** Grab entire seasons or arbitrary episode ranges in one command.
- **Quality Selection:** Choose between available resolutions (1080p, 720p, 360p) per batch.
- **Parallel Downloads:** Configurable worker count to saturate your bandwidth without overwhelming the host.
- **Resume Support:** Interrupted downloads can be resumed — partial files are detected and appended to instead of re-downloaded.
- **Kwik Resolution:** Automatically resolves AnimePahe's Kwik CDN redirect chain to extract direct download links.

### User Experience

- **CLI Flags:** Full argument support for automation and quick staging.
- **Progress Tracking:** Per-episode and overall progress bars with speed and ETA.
- **Automatic File Naming:** Episodes are saved with clean, organized filenames (`Anime Name - EP01 [1080p].mkv`).

---

## Architecture Design

PaheBatcher follows a **Pipeline Architecture** where data flows through discrete, independent stages.

### 1. The API Layer (Scraper)

- **URL Parser:**
  - Accepts the AnimePahe URL directly to stage the download process instantly.
- **Episode Fetcher:**
  - Retrieves the full episode list for the provided URL.
  - Resolves episode pages and extracts Kwik embed links.
- **Session Management:**
  - Maintains a persistent `requests.Session` with appropriate headers and cookies.
  - Delegates Cloudflare-challenged requests to FlareSolverr to retrieve valid clearance cookies.

### 2. The Resolver (Link Extractor)

- **Kwik Resolver:**
  - Follows the Kwik redirect chain (embed page → redirect → CDN).
  - Extracts the final direct `.mp4`/`.mkv` URL from the obfuscated Kwik page.
- **Quality Filter:**
  - Parses available quality variants per episode.
  - Selects the closest match to the user's requested resolution.

### 3. The Download Manager

- **Worker Pool:**
  - Uses `concurrent.futures.ThreadPoolExecutor` for concurrent downloads.
  - Configurable concurrency limit to avoid rate-limiting or bans.
- **Chunked Streaming:**
  - Downloads in streamed chunks to support large files without high memory usage.
  - Writes chunks to disk incrementally.
- **Resume Logic:**
  - Checks for existing partial files and sends HTTP `Range` headers to resume from the last byte.
- **File Organizer:**
  - Creates anime-named output directories.
  - Sanitizes filenames for cross-platform compatibility.

---

## Output Structure

Downloaded files are organized automatically:

```
output/
├── Jujutsu Kaisen/
│   ├── Jujutsu Kaisen - EP01 [1080p].mkv
│   ├── Jujutsu Kaisen - EP02 [1080p].mkv
│   ├── Jujutsu Kaisen - EP03 [1080p].mkv
│   └── ...
├── Frieren - Beyond Journey's End/
│   ├── Frieren - Beyond Journey's End - EP01 [720p].mkv
│   └── ...
```

---

## Installation

### Prerequisites

- Python 3.7 or higher
- A stable internet connection
- **[FlareSolverr](https://github.com/FlareSolverr/FlareSolverr)** (Required — used to bypass Cloudflare anti-bot protection. See their repository for [installation and setup instructions](https://github.com/FlareSolverr/FlareSolverr#installation))
- [ffmpeg](https://ffmpeg.org/) (optional — only if merging separate audio/video streams is needed)

### Setup

```bash
# Clone the repository
git clone https://github.com/smolfiddle/pahebatcher.git
cd pahebatcher

# Install dependencies
pip install -r requirements.txt
```

### Dependencies

| Package          | Purpose                                     |
| :--------------- | :------------------------------------------ |
| `requests`       | HTTP session handling and API communication |
| `beautifulsoup4` | HTML parsing for Kwik page extraction       |
| `tqdm`           | Terminal progress bars                      |
| `colorama`       | Cross-platform colored terminal output      |

---

## Usage & CLI Arguments

PaheBatcher requires an AnimePahe anime URL to run. It does not have an interactive mode, allowing for quick staging and seamless integration into scripts.

### CLI Arguments

```bash
python3 pahebatcher.py <anime_url> [options]
```

| Argument         | Description                                                             | Default                    |
| :--------------- | :---------------------------------------------------------------------- | :------------------------- |
| `anime_url`      | The URL of the anime on AnimePahe (e.g., `https://animepahe.ru/a/...`). | **Required**               |
| `--quality`      | Preferred resolution: `1080`, `720`, `360`.                             | `1080`                     |
| `--start`        | Starting episode number.                                                | `1`                        |
| `--end`          | Ending episode number. Use `0` for all available.                       | `0`                        |
| `--output`       | Output directory path.                                                  | `./out`                    |
| `--workers`      | Number of parallel download workers.                                    | `2`                        |
| `--sub`          | Prefer subbed episodes.                                                 | `True`                     |
| `--dub`          | Prefer dubbed episodes (overrides `--sub`).                             | `False`                    |
| `--resume`       | Resume interrupted downloads from partial files.                        | `True`                     |
| `--flaresolverr` | URL for the FlareSolverr instance.                                      | `http://localhost:8191/v1` |

**Examples:**

Download all episodes of an anime in 720p:

```bash
python3 pahebatcher.py "https://animepahe.ru/a/1234" --quality 720
```

Download episodes 5 through 12 with 4 parallel workers:

```bash
python3 pahebatcher.py "https://animepahe.ru/a/1234" --start 5 --end 12 --workers 4
```

Download dubbed episodes to a specific folder using a remote FlareSolverr instance:

```bash
python3 pahebatcher.py "https://animepahe.ru/a/5678" --dub --output ~/Anime/OnePiece --flaresolverr "http://192.168.1.50:8191/v1"
```

---

## Troubleshooting

| Issue                               | Solution                                                                                                                                        |
| :---------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------- |
| **Cloudflare 403 / CAPTCHA errors** | Ensure FlareSolverr is running and accessible at the URL provided in `--flaresolverr`. Restart FlareSolverr if its browser session has expired. |
| **Kwik resolution fails**           | The Kwik page structure may have changed. Ensure you're on the latest version of PaheBatcher. FlareSolverr may also need a restart.             |
| **Downloads are slow**              | Lower `--workers` to avoid rate-limiting, or increase it if your bandwidth is underutilized.                                                    |
| **403 Forbidden on media CDN**      | AnimePahe may be blocking rapid requests. Reduce `--workers` to 1 or 2 and add delays between episodes.                                         |
| **Partial file not resuming**       | Delete the partial file and re-run. Some servers don't support `Range` headers.                                                                 |

---

## Disclaimer

This tool is intended for personal and educational use only. Downloading copyrighted content without permission may violate the terms of service of the source website and the laws of your jurisdiction. The author assumes no responsibility for misuse.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.
