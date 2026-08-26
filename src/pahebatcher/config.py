"""Constants and configuration for pahebatcher."""

HLS_WORKERS = 24
RETRY_ATTEMPTS = 5
RETRY_BASE_DELAY = 0.5
REQUEST_DELAY = 0.4
VERSION = "3.3.0"
_SEG_HINT_BYTES = 188 * 512
_KWIK_DOMAINS = r"kwik\.(?:si|cx|pw|gg|me|net|to|in|cc)"
_UUID_RE_STR = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
