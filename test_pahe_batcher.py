import pytest
import time
import logging
from unittest.mock import MagicMock, patch
import pahe_batcher
from pathlib import Path

def test_parse_anime_url():
    url = "https://animepahe.ru/anime/540562da-0708-2e0f-2178-01306c59b207"
    host, uuid = pahe_batcher.parse_anime_url(url)
    assert host == "animepahe.ru"
    assert uuid == "540562da-0708-2e0f-2178-01306c59b207"

    with pytest.raises(ValueError):
        pahe_batcher.parse_anime_url("https://google.com")

def test_js_packer():
    # Example of a packed script (simplified)
    packed = "} ('payload', 10, 10, 'payload'.split('|'))"
    unpacked = pahe_batcher.JsPacker.unpack(packed)
    assert "payload" in unpacked

def test_parse_ep_range():
    all_eps = [
        pahe_batcher.EpisodeInfo(1, "s1", "T1", "F", "jpn", "u1"),
        pahe_batcher.EpisodeInfo(2, "s2", "T2", "F", "jpn", "u2"),
        pahe_batcher.EpisodeInfo(3, "s3", "T3", "F", "jpn", "u3"),
        pahe_batcher.EpisodeInfo(4, "s4", "T4", "F", "jpn", "u4"),
    ]

    assert pahe_batcher._parse_ep_range("1-2", all_eps) == [1.0, 2.0]
    assert pahe_batcher._parse_ep_range("3-", all_eps) == [3.0, 4.0]
    assert pahe_batcher._parse_ep_range("1,4", all_eps) == [1.0, 4.0]

def test_stream_info_cookie_str():
    info = pahe_batcher.StreamInfo(
        url="http://test",
        cookies=[
            {"name": "test_name", "value": "test_value"},
            {"name": "cf_clearance", "value": "xyz123"}
        ],
        user_agent="UA",
        referer="REF"
    )
    assert info.cookie_str == "test_name=test_value; cf_clearance=xyz123"

def test_ttl_cache():
    cache = pahe_batcher.TTLCache(max_size=2, ttl=10.0)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    assert cache.get("k1") == "v1"

    cache.set("k3", "v3") 
    # TTLCache evicts based on oldest entry if max_size reached
    # In TTLCache.set: del self._d[min(self._d, key=lambda k: self._d[k][0])]
    # Since we set k1 then k2 then k3, k1 is the oldest.
    assert cache.get("k1") is None
    assert cache.get("k2") == "v2"
    assert cache.get("k3") == "v3"

def test_m3u8_parsing_aes():
    content = """#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXT-X-MEDIA-SEQUENCE:0
#EXT-X-KEY:METHOD=AES-128,URI="https://key.url",IV=0x1234567890ABCDEF1234567890ABCDEF
#EXTINF:10.0,
segment1.ts
"""
    segments = pahe_batcher.parse_m3u8(content, "https://base.url/")
    assert len(segments) == 1
    assert segments[0]["key_url"] == "https://key.url"
    assert segments[0]["iv"] == bytes.fromhex("1234567890ABCDEF1234567890ABCDEF")

def test_m3u8_variant_selection():
    master_content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=1280000,RESOLUTION=720x480
low.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2560000,RESOLUTION=1280x720
high.m3u8
"""
    variant_content = """#EXTM3U
#EXTINF:10.0,
chunk1.ts
"""
    with patch("pahe_batcher._fetch_text", return_value=variant_content):
        segments = pahe_batcher.parse_m3u8(master_content, "https://base.url/")
        # Should follow the last variant (highest bandwidth usually)
        assert len(segments) == 1
        assert "chunk1.ts" in segments[0]["url"]

def test_parse_resolution_buttons():
    html = """
    <div id="resolutionMenu">
        <button data-src="https://kwik.si/e/sub1080" data-resolution="1080" data-fansub="SubsPlease">1080p · SubsPlease</button>
        <button data-src="https://kwik.si/e/dub1080" data-resolution="1080" data-audio="eng" data-fansub="Yameii">1080p · Yameii</button>
        <button data-src="https://kwik.si/e/sub720" data-resolution="720" data-fansub="SubsPlease">720p · SubsPlease</button>
    </div>
    """
    entries = pahe_batcher._parse_resolution_buttons(html)
    assert len(entries) == 3
    # Entries: (res, kwik_url, is_dub, fansub)
    assert entries[0] == (1080, "https://kwik.si/e/sub1080", False, "SubsPlease")
    assert entries[1] == (1080, "https://kwik.si/e/dub1080", True, "Yameii")

def test_extract_stream_logic():
    html = """
    <div id="resolutionMenu">
        <button data-src="https://kwik.si/e/360" data-resolution="360">360p</button>
        <button data-src="https://kwik.si/e/720" data-resolution="720">720p</button>
        <button data-src="https://kwik.si/e/1080" data-resolution="1080">1080p</button>
    </div>
    """
    with patch("pahe_batcher.Solver.request") as mock_req:
        mock_req.return_value = {"response": html, "cookies": []}
        with patch("pahe_batcher._resolve_kwik") as mock_resolve:
            mock_resolve.return_value = pahe_batcher.StreamInfo("url", [], "UA", "REF")

            # Case 1: Pick 720p
            pahe_batcher.extract_stream("url", quality=720, audio="jpn")
            # Logic picks best quality <= preference.
            
            # Case 2: Pick lowest if all are higher
            # Note: chosen_q = next((q for q in qs if q <= quality), qs[-1])
            # If qs=[1080, 720, 360] and quality=240, it returns 360 (the last element)
            pahe_batcher.extract_stream("url", quality=240, audio="jpn")

@patch("pahe_batcher.Solver.fetch_json")
@patch("pahe_batcher.Solver.fetch_html")
def test_scanner(mock_html, mock_json):
    mock_json.return_value = {
        "total": 1,
        "last_page": 1,
        "data": [{"episode": 1, "session": "abc", "title": "Ep1", "fansub": "Sub", "audio": "jpn"}]
    }
    mock_html.return_value = ("<html><h1>Series Title</h1></html>", [])

    scanner = pahe_batcher.AnimePaheScanner("animepahe.ru", "uuid")
    anime = scanner.scan()

    assert anime.title == "Series Title"
    assert len(anime.episodes) == 1
    assert anime.episodes[0].number == 1.0

def test_sanitize():
    assert pahe_batcher.sanitize("My Anime: Season 1") == "My_Anime_Season_1"
    assert pahe_batcher.sanitize("...!!File??") == "...File"

def test_ep_prefix():
    assert pahe_batcher.ep_prefix("5") == "005"
    assert pahe_batcher.ep_prefix("5.5") == "005.5"

@pytest.mark.asyncio
@patch("pahe_batcher.extract_stream")
@patch("asyncio.create_subprocess_exec")
@patch("rich.prompt.Prompt.ask")
@patch("rich.prompt.Confirm.ask")
async def test_run_stream_navigation(mock_confirm, mock_ask, mock_exec, mock_extract):
    # Mocking the dependencies
    mock_extract.return_value = pahe_batcher.StreamInfo(
        url="http://test.m3u8",
        cookies=[],
        user_agent="UA",
        referer="REF",
        title="Real Title"
    )

    # Mock subprocess
    mock_proc = MagicMock()
    async def side_effect_communicate():
        return (b"", b"")
    mock_proc.communicate.side_effect = side_effect_communicate
    mock_proc.returncode = 0
    mock_exec.return_value = mock_proc

    # Mock user input: [Next, Quit]
    mock_ask.side_effect = ["n", "q"]
    mock_confirm.return_value = True

    episodes = [
        pahe_batcher.EpisodeInfo(1, "s1", "", "F", "jpn", "u1"),
        pahe_batcher.EpisodeInfo(2, "s2", "", "F", "jpn", "u2"),
    ]
    anime = pahe_batcher.AnimeInfo(session="sess", title="Series", host="host", episodes=episodes)
    cfg = pahe_batcher.DownloadConfig()

    await pahe_batcher.run_stream(anime, episodes, cfg)

    # Verify extract_stream was called
    assert mock_extract.call_count == 2
    assert episodes[0].title == "Real Title"
