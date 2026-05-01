import pytest
from unittest.mock import MagicMock, patch
import pahe_batcher

def test_pahe_parse_url():
    url = "https://animepahe.ru/anime/540562da-0708-2e0f-2178-01306c59b207"
    host, uuid = pahe_batcher._pahe_parse_url(url)
    assert host == "animepahe.ru"
    assert uuid == "540562da-0708-2e0f-2178-01306c59b207"

    with pytest.raises(ValueError):
        pahe_batcher._pahe_parse_url("https://google.com")

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

def test_format_cookies():
    cookies = [
        {"name": "test_name", "value": "test_value"},
        {"name": "cf_clearance", "value": "xyz123"}
    ]
    formatted = pahe_batcher.format_cookies(cookies)
    assert formatted == "test_name=test_value; cf_clearance=xyz123"

def test_adaptive_compressor():
    data = b"hello world" * 100
    compressed, was_compressed = pahe_batcher.AdaptiveCompressor.compress(data)
    assert was_compressed is True
    assert len(compressed) < len(data)
    
    decompressed = pahe_batcher.AdaptiveCompressor.decompress(compressed, was_compressed)
    assert decompressed == data

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

def test_lru_cache():
    cache = pahe_batcher.LRUCache(max_size=2, ttl=10.0)
    cache.set("k1", "v1")
    cache.set("k2", "v2")
    assert cache.get("k1") == "v1" # k1 is now most recent
    
    cache.set("k3", "v3") # k2 should be evicted (LRU)
    assert cache.get("k1") == "v1"
    assert cache.get("k2") is None
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
    segments = pahe_batcher.parse_m3u8_segments(content, "https://base.url/")
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
        segments = pahe_batcher.parse_m3u8_segments(master_content, "https://base.url/")
        # Should follow the last variant (highest bandwidth usually)
        assert len(segments) == 1
        assert "chunk1.ts" in segments[0]["url"]

def test_quality_selection_logic():
    html = """
    <button data-src="https://kwik.si/e/360" data-resolution="360">360p</button>
    <button data-src="https://kwik.si/e/720" data-resolution="720">720p</button>
    <button data-src="https://kwik.si/e/1080" data-resolution="1080">1080p</button>
    """
    with patch("pahe_batcher.Solver.request") as mock_req:
        mock_req.return_value = {"response": html, "cookies": []}
        with patch("pahe_batcher._resolve_one_kwik") as mock_resolve:
            mock_resolve.return_value = {"url": "m3u8"}
            
            # Case 1: Pick exact 720p
            pahe_batcher.extract_animepahe_stream("url", preferred_quality=720)
            # The logic picks best quality <= preference.
            
            # Case 2: Pick 720p when 1080p is preferred but missing (not applicable here, but logic check)
            # Case 3: Pick lowest if all are higher
            pahe_batcher.extract_animepahe_stream("url", preferred_quality=240)
            # It should pick 360p (the lowest available)

def test_asset_manager_database_ops(tmp_path):
    db_file = str(tmp_path / "test.db")
    db = pahe_batcher.VaultDB(db_file)
    mgr = pahe_batcher.AssetManager(db)
    
    # 1. Add Asset
    aid = mgr.add_asset("http://source", "Title", "1")
    assert aid == 1
    
    # 2. Store Chunk (Deduplication Check)
    data = b"chunk_data"
    mgr.store_single_chunk(aid, 0, data)
    
    # Verify it exists
    completed = mgr.get_completed_segments(aid)
    assert 0 in completed
    
    # Store same data for another asset
    aid2 = mgr.add_asset("http://source2", "Title2", "2")
    mgr.store_single_chunk(aid2, 0, data)
    
    # Verify chunks table has only 1 entry (deduplication)
    conn = db.pool.get()
    count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert count == 1
    db.pool.put(conn)
    
    # 3. Update status
    mgr.update_status(aid, "complete", total_bytes=10)
    asset = mgr.get_asset(aid)
    assert asset["status"] == "complete"
    assert asset["total_bytes"] == 10
    
    # 4. Meta
    mgr.store_meta(aid, "key", "value")
    assert mgr.get_meta(aid, "key") == "value"
    
    db.close()

import time
def test_lru_cache_expiry():
    cache = pahe_batcher.LRUCache(max_size=10, ttl=0.1)
    cache.set("expired", "value")
    time.sleep(0.2)
    assert cache.get("expired") is None

@pytest.mark.asyncio
@patch("pahe_batcher.extract_animepahe_stream")
@patch("asyncio.create_subprocess_exec")
@patch("rich.prompt.Prompt.ask")
@patch("rich.prompt.Confirm.ask")
async def test_run_stream_navigation(mock_confirm, mock_ask, mock_exec, mock_extract):
    # Mocking the dependencies
    mock_extract.return_value = {
        "url": "http://test.m3u8",
        "user_agent": "UA",
        "referer": "REF",
        "cookies": [],
        "title": "Real Title"
    }
    
    # Mock subprocess
    mock_proc = MagicMock()
    # Use AsyncMock for methods that are awaited
    mock_proc.communicate = MagicMock()
    async def side_effect_communicate():
        return (b"", b"")
    mock_proc.communicate.side_effect = side_effect_communicate
    
    mock_proc.returncode = 0
    mock_exec.return_value = mock_proc
    
    # Mock user input: [Next, Quit]
    mock_ask.side_effect = ["n", "q"]
    mock_confirm.return_value = True
    
    episodes = [
        pahe_batcher.EpisodeInfo(1, "s1", "—", "F", "jpn", "u1"),
        pahe_batcher.EpisodeInfo(2, "s2", "—", "F", "jpn", "u2"),
    ]
    cfg = pahe_batcher.DownloadConfig()
    
    await pahe_batcher._run_stream("Series", episodes, cfg)
    
    # Verify titles were updated
    assert episodes[0].title == "Real Title"
    assert mock_exec.call_count == 2
