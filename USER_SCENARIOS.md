# Pahebatcher — User Scenarios & Complete Flow

*All paths through the tool, all personas, all edge cases.*

---

## Entry Points

```
pahebatcher [URL] [options]              # Full CLI
pahebatcher                              # Interactive search
pahebatcher config show                  # View settings
pahebatcher config set KEY VALUE         # Change default
pahebatcher config reset                 # Factory defaults
python -m pahebatcher ...                # Module invocation
make run                                 # Makefile shortcut
```

---

## Scenario A: First-Time User (No Config, Interactive)

**Persona:** New user. Has FlareSolverr running. Wants to download a full series.

**Flow:**

```
1. $ pahebatcher
   → Banner displays
   → FlareSolverr health check passes

2. "Search Anime (or 'q' to quit): spy x family"
   → Searches AnimePahe API
   → Table appears: 6 results, titles, types, years, episodes, scores

3. "Select # (1-6): 1"
   → Selects "Spy x Family"
   → Scanner discovers all SUB/DUB variant sessions
   → Fetches episode list (25 episodes)
   → Prints: "✓ Spy x Family — 50 episodes (1–25)"

4. Action menu:
   ┌──────────────────────────────────────────┐
   │  1  Download  · save .mp4 files          │
   │  2  Stream    · play in MPV              │
   │  3  Sessions & Cache                     │
   │  4  List      · show episode table       │
   │  5  Exit                                 │
   └──────────────────────────────────────────┘
   → "Select action (1): 1"

5. Episode selection menu:
   ┌──────────────────────────────────────────┐
   │  A  All episodes                         │
   │  R  Range    e.g. 1-12  or  1,4,7       │
   │  L  Toggle   interactive checklist       │
   │  N  Latest N                             │
   │  S  Skip                                 │
   └──────────────────────────────────────────┘
   → "Select mode (A): a"
   → ✓ All 25 episodes selected

6. Settings wizard (first run — config file does not exist yet):
   → Quality:  1080p (press 3)
   → Audio:    SUB (press 1)
   → Output:   ./downloads/Spy_x_Family (Enter)
   → Parallel: 2 (Enter)
   → Workers:  24 (Enter)

7. Confirmation panel:
   ┌──────────────────────────────────────────┐
   │  Series:    Spy x Family                  │
   │  Episodes:  25  (1–25)                   │
   │  Audio:     SUB                          │
   │  Quality:   1080p                         │
   │  Output:    ./downloads/Spy_x_Family     │
   │  Est. size: ~3750 MB  (~150 MB/ep × 25) │
   └──────────────────────────────────────────┘
   → "Start download? [y/n] (y): y"

8. Download dashboard (Live):
   ┌────────────────────────────────────────────────────────────┐
   │   Episode Title          Progress    Segments  %  Speed  ETA │
   │ ─────────────────────────────────────────────────────────── │
   │   ⟳ Ep 1 — Resolving...   ━━━━━━━━  0/100   0%  —       —  │
   │   ⋯ Ep 2 — Pending...     ━━━━━━━━  0/100   0%  —       —  │
   │   ⋯ Ep 3 — Pending...     ━━━━━━━━  0/100   0%  —       —  │
   │   ...                                                        │
   └────────────────────────────────────────────────────────────┘
   → Ep 1 resolves → ⌛ queued → downloading (white bar fills)
   → Ep 2 begins resolving while Ep 1 downloads
   → Pipeline continues until all 25 complete

9. Summary table:
   ┌─────────────────────────────────────────────────────────────┐
   │  Ep  Title                      Status          Size  File   │
   │ ─────────────────────────────────────────────────────────── │
   │   1  Spy x Family Ep. 1         ✓  done     154 MB  Ep 00… │
   │   2  Spy x Family Ep. 2         ✓  done     148 MB  Ep 00… │
   │  ...                                                         │
   │   ✓ 25 completed                                            │
   │   Time:      3m 12s                                         │
   │   Saved to:  ./downloads/Spy_x_Family                       │
   └─────────────────────────────────────────────────────────────┘

10. Config auto-saved: ./pahebatcher.toml
    → quality = 1080, audio_lang = jpn, max_parallel = 2, hls_workers = 24, output_dir = .
    → Config file now exists → wizard will be skipped on next run
```

**Key states visited:** `resolver → search → scan → menu → episodes → wizard → confirm → dashboard (resolving → queued → downloading → remuxing → done) → summary → config save`


## Scenario B: Returning User (Config Exists, Wizard Skipped)

**Persona:** Used pahebatcher before. Config file exists. Wants 3 latest English dub episodes at 720p (one-off override).

**Flow:**

```
1. $ pahebatcher "URL" --latest 3 --audio eng -q 720
   → Banner displays
   → FlareSolverr health check
   → Scanner discovers series

2. No wizard (--latest is a "scripted" flag)
   → Uses: quality=720 (CLI), audio=eng (CLI), parallel=2 (config), workers=24 (config)
   → Episode selection: latest 3 (automatic)
   → Confirmation skipped (scripted mode)
   → Download dashboard → summary
```

**Skip points:** search (URL provided), action menu (--latest implies download), wizard (scripted flag), confirmation (scripted mode).


## Scenario C: Returning User (Config Exists, Interactive, Wizard Skipped)

**Persona:** Used pahebatcher before. Config saved. Wants same settings as last time, different anime.

**Flow:**

```
1. $ pahebatcher
   → Banner → FlareSolverr → search "frieren" → select result
   → Scanner: "✓ Frieren: Beyond Journey's End — 28 episodes"

2. Action menu → select 1 (Download)

3. Episode selection → "a" (All)

4. WIZARD SKIPPED (config file is customized)
   → Uses saved: quality=1080, audio=jpn, parallel=2, workers=24
   → Output: ./downloads/Frieren_Beyond_Journeys_End (auto-computed from anime title)

5. Confirmation:
   ┌──────────────────────────────────────────┐
   │  Series:    Frieren: Beyond Journey's End │
   │  Episodes:  28  (1–28)                   │
   │  Audio:     SUB                          │
   │  Quality:   1080p                         │
   │  Output:    ./downloads/Frieren_Beyond…  │
   └──────────────────────────────────────────┘
   → "Start download? [y/n] (y): y"

6. Download → summary
```

**Key difference from Scenario A:** Steps 4 (wizard) is absent. User goes from episode selection directly to confirmation.


## Scenario D: Config CLI User (No Download, Manage Settings)

**Persona:** Wants to tweak defaults without running the full tool.

**Flow:**

```
1. $ pahebatcher config show
   ┌──────────────┬───────┬─────────┐
   │ Key          │ Value │ Default │
   ├──────────────┼───────┼─────────┤
   │ quality      │ 1080  │ 1080    │
   │ audio_lang   │ jpn   │ jpn     │
   │ max_parallel │ 2     │ 2       │
   │ hls_workers  │ 24    │ 24      │
   │ output_dir   │ .     │ .       │
   │ keep_temp    │ False │ False   │
   └──────────────┴───────┴─────────┘

2. $ pahebatcher config set quality 720
   → ✓ quality = 720

3. $ pahebatcher config set max_parallel 4
   → ✓ max_parallel = 4

4. $ vim pahebatcher.toml    # OR open in IDE
   # Edit quality = "360" directly

5. $ pahebatcher "URL" --all
   → Uses: quality=360 (from edited config), parallel=4 (from config)
   → No wizard (config customized)
   → Downloads all episodes at 360p, 4 concurrent

6. $ pahebatcher config reset
   → ✓ Configuration reset to defaults
   → Next run: wizard shows again (config at factory defaults)
```

**Skip points:** No FlareSolverr check needed for config commands. No search. No scan. Pure config manipulation.

**Note:** Future runs can override any config value: `--quality 1080` while config says 720. CLI flag wins.


## Scenario E: Stream Mode

**Persona:** Wants to watch in MPV without downloading.

**Flow:**

```
1. $ pahebatcher "URL" --stream --audio eng
   OR interactive: search → select → action menu → 2 (Stream)
   → No wizard (--stream is scripted; interactive uses config or wizard)

2. Streaming starts:
   ╭────────────────▶ Live Playback ────────────────────────╮
   │  Frieren: Beyond Journey's End                          │
   │  ▶  Ep 1  Frieren: Beyond Journey's End Ep. 1          │
   │     Audio: SUB  (DUB available — press A to switch)     │
   │     Episode 1 of 28                                    │
   │  ─────────────────────────────────────────────────────  │
   │  Close MPV window to return to controls                 │
   ╰────────────────────────────────────────────────────────╯

3. MPV plays episode. User closes MPV.

4. Post-playback controls:
   ╭────────────────■ Playback Ended ───────────────────────╮
   │  Frieren: Beyond Journey's End                          │
   │  ■  Ep 1  Frieren: Beyond Journey's End Ep. 1  SUB     │
   │  ─────────────────────────────────────────────────────  │
   │  Next  ·  Audio→DUB  ·  Replay  ·  Select  ·  Quit     │
   ╰────────────────────────────────────────────────────────╯
   → " (N)ext  (A)udio  (R)eplay  (S)elect  (Q)uit (n): n"

5. "n" → advances to Ep 2, resolves stream, plays
   "a" → switches to DUB, rebuilds playlist, resolves, plays
   "s" → shows episode table, jump to any episode number
   "r" → replays current episode
   "q" → exits

6. "Playback session ended." → back to shell
```

**Key states:** `resolve → play (Live panel) → post-playback panel (controls) → repeat or quit`


## Scenario F: Session Resume (Crash Recovery)

**Persona:** Interrupted mid-download. Wants to continue where they left off.

**Flow:**

```
1. $ pahebatcher "URL"
   → Search → scan
   → "✓ Spy x Family [PARTIAL DOWNLOAD FOUND]"
     (has_session = True — cache directory exists)

2. Action menu → 1 (Download) → select episodes
   → Wizard skipped (config exists)

3. Confirmation panel shows:
   ┌───────────────────────────────────────────────┐
   │  Reusing: 847 segments from previous session  │
   └───────────────────────────────────────────────┘

4. Download dashboard:
   → Already-completed episodes: ✓ (already exists) — skipped
   → Partially downloaded episodes: only missing segments fetched
   → Remaining episodes: full download
   → Segment counter starts from 847 (already done), not 0

5. Power failure at segment 1100/1200:
   → All segments 0–1099 are already on disk (atomic .tmp→.ts writes)
   → Segments 1100–1199 are NOT on disk (were mid-download or not reached)
   → On restart, done_indices() returns {0,1,2,...,1099}
   → Downloads picks up at segment 1100
   → Zero progress lost
```

**Alternative flow via Session Manager:**

```
1. $ pahebatcher (no URL)
   → If you don't know the URL, search normally

2. Action menu → 3 (Sessions & Cache)
   → Table shows cached sessions:
   ┌───────────────────────────────────────────────┐
   │ #  Anime Title              Eps  Segs   Size  │
   │ 1  Spy x Family             25   847   8.2 GB │
   │ 2  Frieren                  5    120   1.1 GB │
   └───────────────────────────────────────────────┘

3. [R]esume → 1 → restarts tool with Spy x Family URL
   → Same flow as above

4. [D]elete → 1 → removes Spy x Family cache
   [C]lear All → wipes entire pahe_cache/
   [B]ack → returns to action menu
```


## Scenario G: List Only (No Download)

**Persona:** Wants to see what episodes a series has without downloading.

**Flow:**

```
1. $ pahebatcher "URL" --list
   → Scanner discovers series
   → Episode table prints:
   ┌─────┬──────────────────────────────────┬───────┐
   │ Ep   │ Title                           │ Audio │
   ├─────┼──────────────────────────────────┼───────┤
   │    1 │ Spy x Family Ep. 1              │ SUB   │
   │    1 │ Spy x Family Ep. 1              │ DUB   │
   │    2 │ Spy x Family Ep. 2              │ SUB   │
   │    2 │ Spy x Family Ep. 2              │ DUB   │
   │  ...                                    │       │
   └─────┴──────────────────────────────────┴───────┘
   → Exits (no action menu, no download)
```


## Scenario H: Interactive Toggle Checklist

**Persona:** Wants to pick specific episodes from a large series. Not range-based.

**Flow:**

```
1. pahebatcher → search → select → action (1) → episode selection → L

2. Toggle checklist appears:
   ┌──────────────────────────────────┐
   │      Spy x Family                │
   ├───┬─────┬────────────────────────┤
   │   │ Ep  │ Title                  │
   ├───┼─────┼────────────────────────┤
   │ ✓ │   1 │ Spy x Family Ep. 1     │
   │ ✓ │   2 │ Spy x Family Ep. 2     │
   │   │   3 │ Spy x Family Ep. 3     │
   │ ✓ │   4 │ Spy x Family Ep. 4     │
   │  ...                              │
   └───┴─────┴────────────────────────┘
   → a=all  n=none  <num>=toggle  done=confirm

3. User types: "1-4,7,10,12"
   → Toggles episodes 1-4, 7, 10, 12
   → ✓ marks appear/disappear

4. "done" → confirms selection
   → ✓ 7 episodes selected
   → Proceeds to wizard/confirmation/download
```


## Scenario I: Mid-Session SUB/DUB Switch (Stream Mode)

**Persona:** Streaming in SUB, wants to check DUB quality mid-session.

**Flow:**

```
1. Currently watching Ep 5 in SUB. MPV closes.

2. Post-playback panel:
   Next  ·  Audio→DUB  ·  Replay  ·  Select  ·  Quit

3. "a" (Audio)
   → audio_pref switches from "jpn" to "eng"
   → playlist rebuilt with DUB variants
   → idx clamped to valid range for new playlist
   → Ep 5 resolves with DUB audio track
   → Plays in MPV with [DUB] tag

4. Next episode (Ep 6): also plays in DUB (audio_pref persists through session)

5. Mid-Ep 8, user switches back: "a" → SUB
   → audio_pref = "jpn", playlist rebuilt, continues in SUB
```


## Scenario J: Config File Editing (IDE Flow)

**Persona:** Prefers editing a file over `config set` commands.

**Flow:**

```
1. Open ./pahebatcher.toml in IDE:
   # pahebatcher configuration
   quality = 720
   audio_lang = 'jpn'
   max_parallel = 4
   hls_workers = 24
   output_dir = '.'
   keep_temp = false

2. Edit: quality = 360, max_parallel = 6

3. $ pahebatcher "URL" --all
   → Loads config: quality=360, parallel=6
   → Config customized → wizard skipped
   → Downloads all episodes at 360p, 6 concurrent

4. To check current values:
   $ pahebatcher config show
   ┌──────────────┬───────┬─────────┐
   │ quality      │ 360   │ 1080    │
   │ max_parallel │ 6     │ 2       │
   └──────────────┴───────┴─────────┘
```

---

## Edge Cases & Error Paths

### No FlareSolverr running
```
$ pahebatcher
→ ✗ FlareSolverr not responding
→ Prints Docker command
→ sys.exit(1)
```
Config commands (`config show/set/reset`) bypass FlareSolverr entirely.

### Invalid URL
```
$ pahebatcher "https://google.com"
→ ✗ Not an AnimePahe URL
→ sys.exit(1)
```

### No search results
```
$ pahebatcher
→ Search: "xyznonexistent123"
→ ⚠ No results found
→ Prompts to search again or 'q' to quit
```

### Empty episode selection
```
→ Episode selection → S (Skip)
→ Returns to action menu (or exits if scripted)
```

### Episodes already downloaded
```
→ Confirmation → download
→ Episodes with MP4 files on disk: ✓ (already exists) — immediate
→ No re-download, no cache usage
```

### Ctrl+C during download
```
→ Aborts immediately
→ Active segment fetches terminate
→ Cache state: all .tmp→.ts renamed segments are safe on disk
→ Restart picks up from last completed segment
```

### Resolution timeout
```
→ Resolver: "Resolution timed out for Ep 3"
→ Dashboard: ✗ Ep 3: Resolution Timeout
→ Continue to next episodes
→ Summary: ✓ 24 completed | ✗ 1 failed
```

### No DUB variant available
```
→ --audio eng requested but series has no DUB
→ Fallback: "Preferred audio eng not found for Ep 1, falling back to jpn"
→ Downloads SUB instead
→ Audio badge in summary still shows DUB (requested preference)
→ In stream mode: Audio→DUB button still appears (extract_stream may find DUB on play page even if API doesn't list it)
```

### Config file corrupted
```
→ Malformed TOML → tomllib.TOMLDecodeError
→ Falls through to Factory defaults (no wizard skip)
→ User sees wizard as if first run
→ wizard saves clean file on completion
```

---

## Decision Tree Summary

```
pahebatcher
│
├── config show/set/reset  →  Display, modify, or reset settings  →  exit
│
├── [no args]
│   ├── FlareSolverr?      →  No  →  print Docker cmd  →  exit
│   ├── search query       →  No results  →  retry/quit
│   │                      →  select result  →  scan series
│   ├── action menu
│   │   ├── 1 Download
│   │   │   ├── episode selection (A/R/L/N/S)
│   │   │   │   └── skip?  →  back to action menu
│   │   │   ├── config customized?  →  Yes  →  skip wizard
│   │   │   │                        →  No   →  wizard prompts  →  save config
│   │   │   ├── confirmation
│   │   │   │   └── no?  →  back to action menu
│   │   │   ├── resolve stream URLs (serial, FlareSolverr)
│   │   │   ├── download segments (parallel, aiohttp)
│   │   │   ├── mux via ffmpeg
│   │   │   └── summary table  →  exit
│   │   │
│   │   ├── 2 Stream
│   │   │   ├── same episode selection + wizard logic
│   │   │   ├── resolve  →  play in MPV  →  post-playback controls
│   │   │   │   ├── N/P  →  navigate
│   │   │   │   ├── A    →  switch audio  →  rebuild playlist
│   │   │   │   ├── R    →  replay
│   │   │   │   ├── S    →  jump to episode
│   │   │   │   └── Q    →  exit
│   │   │   └── "Playback session ended"
│   │   │
│   │   ├── 3 Sessions & Cache
│   │   │   ├── [R]esume  →  restart main with cached URL
│   │   │   ├── [D]elete  →  remove single session
│   │   │   ├── [C]lear All  →  wipe cache
│   │   │   └── [B]ack  →  back to action menu
│   │   │
│   │   ├── 4 List  →  episode table  →  back to action menu
│   │   └── 5 Exit  →  cleanup orphans  →  exit
│   │
│   └── cleanup orphans (>24h stale cache)
│
├── "URL" --all / --range 1-12 / --latest N
│   └── scripted flow: skip search, skip action menu, skip wizard, skip confirmation
│       └── CLI flags override config for this run
│
├── "URL" --stream
│   └── scripted flow → stream mode, no confirmation
│
└── "URL" --list
    └── scan → episode table → exit
```
