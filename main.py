"""
XiaoZhi Cloud MCP Service (Render.com Deployment)
- 24/7 Cloud DuckDuckGo Web Search
- 24/7 Cloud YouTube Music Engine (mono 24kHz Opus)
- Public HTTPS streaming to XiaoZhi ESP32
"""

import asyncio
import json
import logging
import os
import re
import html
import subprocess
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler
import threading
import websockets
import sys
import time
import urllib.request
import base64

# Configuration from Environment Variables
MCP_WS_URL = os.environ.get(
    "MCP_WS_URL",
    "wss://api.xiaozhi.me/mcp/?token=eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjEwNjg0MjYsImFnZW50SWQiOjIzMjcxMjEsImVuZHBvaW50SWQiOiJhZ2VudF8yMzI3MTIxIiwicHVycG9zZSI6Im1jcC1lbmRwb2ludCIsImlhdCI6MTc4OTEzMTcyMSwiZXhwIjoxODIwNjg5MzIxfQ.AwQdK_IQkQqUJjsq8tcxOgFcJ8q3Nes2zgR-iMSIByt2B8BKTgXKLAo5vQ0f7cPPLhBk-KJ7rmYI8kpnGakWNQ"
)
PORT = int(os.environ.get("PORT", 10000))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")
COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")
YOUTUBE_PROXY = os.environ.get("YOUTUBE_PROXY", "").strip() or os.environ.get("HTTP_PROXY", "").strip()

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", stream=sys.stdout)
logger = logging.getLogger("XiaoZhiCloudMCP")
print(f"=== Starting XiaoZhi Cloud MCP Service (PORT={PORT}) ===", flush=True)

if YOUTUBE_PROXY:
    masked_proxy = re.sub(r'://([^:]+):([^@]+)@', r'://\1:****@', YOUTUBE_PROXY)
    logger.info(f"Loaded YouTube proxy: {masked_proxy}")

# Check for YouTube cookies in environment variables
if os.environ.get("YOUTUBE_COOKIES"):
    try:
        raw_cookie = os.environ["YOUTUBE_COOKIES"].strip()
        # Support base64 encoded cookies to prevent cloud UI newline destruction
        if raw_cookie.startswith("base64:"):
            clean_cookie = base64.b64decode(raw_cookie[7:]).decode("utf-8", errors="ignore")
        else:
            clean_cookie = raw_cookie.replace("\\n", "\n").replace("\\t", "\t").replace("\r\n", "\n")
        
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            f.write(clean_cookie)
        valid_lines = [l for l in clean_cookie.splitlines() if l.strip() and not l.startswith("#")]
        logger.info(f"Loaded YouTube cookies into {COOKIES_FILE}: {len(clean_cookie.splitlines())} total lines, {len(valid_lines)} active cookie entries.")
    except Exception as e:
        logger.warning(f"Failed to write YOUTUBE_COOKIES: {e}")

os.makedirs(MUSIC_DIR, exist_ok=True)

def get_base_url():
    if RENDER_EXTERNAL_URL:
        return RENDER_EXTERNAL_URL
    return f"http://localhost:{PORT}"

# HTTP Server for Health Checks and Audio Streaming
class CloudHTTPHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=MUSIC_DIR, **kwargs)

    def do_HEAD(self):
        clean_path = self.path.split('?')[0].rstrip('/')
        if clean_path in ["", "/health", "/healthz", "/ping"]:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            return
        if self.path.startswith("/music/"):
            self.path = self.path[6:]
        super().do_HEAD()

    def do_GET(self):
        clean_path = self.path.split('?')[0].rstrip('/')
        if clean_path in ["", "/health", "/healthz", "/ping"]:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = json.dumps({
                "status": "online",
                "service": "XiaoZhi Cloud MCP",
                "port": PORT,
                "music_dir_files": len(os.listdir(MUSIC_DIR))
            })
            self.wfile.write(resp.encode("utf-8"))
            return
        
        # Route /music/<filename> to MUSIC_DIR/<filename>
        if self.path.startswith("/music/"):
            self.path = self.path[6:] # Strip /music prefix
            
        super().do_GET()

    def guess_type(self, path):
        if path.endswith(".opus") or path.endswith(".ogg"):
            return "audio/ogg"
        return super().guess_type(path)

    def log_message(self, format, *args):
        if args and str(args[1]).startswith("2"):
            return
        logger.info(f"HTTP Server: {format % args}")

def start_http_server():
    server = HTTPServer(("0.0.0.0", PORT), CloudHTTPHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info(f"Cloud HTTP Server running on 0.0.0.0:{PORT}. Public Base URL: {get_base_url()}")

# Keepalive Pinger to prevent Render Free Tier from idling
def keepalive_worker():
    while True:
        time.sleep(600)  # Ping every 10 minutes
        base = get_base_url()
        if "onrender.com" in base:
            try:
                url = f"{base}/health"
                req = urllib.request.Request(url, headers={"User-Agent": "Render-KeepAlive"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    logger.info(f"Render keepalive ping sent to {url} (status={r.status})")
            except Exception as e:
                logger.warning(f"Render keepalive ping failed: {e}")

# DuckDuckGo Unlimited Cloud Search
def duckduckgo_search(query, max_results=4):
    try:
        clean_query = query.strip()
        logger.info(f"Executing cloud web search for: '{clean_query}'")
        data = f"q={urllib.parse.quote_plus(clean_query)}"
        out = subprocess.check_output([
            'curl', '-s', '-L',
            '-A', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
            '-d', data,
            'https://html.duckduckgo.com/html/'
        ], encoding='utf-8', errors='ignore', timeout=12)
        
        titles = re.findall(r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', out, re.DOTALL)
        snippets = re.findall(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', out, re.DOTALL)
        
        results = []
        for i in range(min(len(titles), len(snippets), max_results)):
            raw_url, raw_title = titles[i]
            title = html.unescape(re.sub(r'<[^>]+>', '', raw_title)).strip()
            snippet = html.unescape(re.sub(r'<[^>]+>', '', snippets[i])).strip()
            match = re.search(r'uddg=([^&]+)', raw_url)
            actual_url = urllib.parse.unquote(match.group(1)) if match else raw_url
            results.append(f"[{i+1}] {title}\nSummary: {snippet}\nSource: {actual_url}")
            
        if not results:
            return f"No results found on the web for '{query}'."
        return "\n\n".join(results)
    except Exception as e:
        logger.error(f"Search error: {e}")
        return f"Error executing web search: {e}"

def sanitize_filename(name):
    clean = re.sub(r'[\\/*?:"<>| ]+', '_', name).strip('_').lower()
    return clean[:60] if clean else "track"

def get_youtube_video_url(query):
    # 1. Direct YouTube HTML search (super fast, ~0.4s, needs no cookies)
    try:
        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        })
        if YOUTUBE_PROXY:
            proxy_handler = urllib.request.ProxyHandler({"http": YOUTUBE_PROXY, "https": YOUTUBE_PROXY})
            opener = urllib.request.build_opener(proxy_handler)
            resp = opener.open(req, timeout=10)
        else:
            resp = urllib.request.urlopen(req, timeout=6)
        with resp as r:
            html_text = r.read().decode("utf-8", errors="ignore")
        vids = re.findall(r'/watch\?v=([a-zA-Z0-9_-]{11})', html_text)
        if vids:
            for v in vids:
                return f"https://www.youtube.com/watch?v={v}"
    except Exception as e:
        logger.warning(f"HTML YouTube search failed for '{query}': {e}")

    # 2. Fallback: yt-dlp search without cookies
    try:
        cmd = ["python", "-m", "yt_dlp", "--default-search", "ytsearch1", "--print", "id"]
        if YOUTUBE_PROXY:
            cmd.extend(["--proxy", YOUTUBE_PROXY])
        cmd.append(f"ytsearch1:{query}")
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        for line in res.stdout.splitlines():
            vid = line.strip()
            if len(vid) == 11 and re.match(r'^[a-zA-Z0-9_-]{11}$', vid):
                return f"https://www.youtube.com/watch?v={vid}"
    except Exception as e:
        logger.warning(f"yt-dlp search fallback failed: {e}")

    return None

def download_youtube_audio(query, temp_raw):
    """
    Direct YouTube audio download using yt-dlp.
    1. Resolves video URL cleanly to avoid HTTP 400 Bad Request on search API.
    2. Downloads format 251/140 audio directly with proxy/cookies.
    """
    has_cookies = os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 10
    
    # Target master studio uploads
    queries = [f"{query} official audio", f"{query} audio", query]
    
    video_url = None
    for q_try in queries:
        video_url = get_youtube_video_url(q_try)
        if video_url:
            logger.info(f"Resolved video URL for '{q_try}': {video_url}")
            break
            
    if not video_url:
        video_url = f"ytsearch1:{query}"

    # Download attempts: with cookies first (if present), then clean fallback
    attempts = []
    if has_cookies:
        attempts.append(("with cookies", ["--cookies", COOKIES_FILE]))
    attempts.append(("clean mode", ["--extractor-args", "youtube:player_client=android,web_creator,ios,web"]))

    proxy_args = ["--proxy", YOUTUBE_PROXY] if YOUTUBE_PROXY else []

    for mode_name, extra_args in attempts:
        cmd = [
            "python", "-m", "yt_dlp",
            "--no-playlist",
            "-f", "ba/b",
            "--socket-timeout", "20",
            "-x", "--audio-format", "opus",
            "-o", temp_raw
        ] + proxy_args + extra_args + [video_url]

        logger.info(f"Downloading YouTube track ({mode_name}{' via proxy' if YOUTUBE_PROXY else ''}) from {video_url}...")
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=75)
        if res.returncode == 0 and os.path.exists(temp_raw) and os.path.getsize(temp_raw) > 10000:
            logger.info(f"Successfully downloaded track from YouTube ({mode_name})!")
            return True
        else:
            err_snippet = res.stderr[:250] if res.stderr else "unknown error"
            logger.warning(f"Attempt failed ({mode_name}): {err_snippet}")

    return False

# Cloud YouTube Music Fetcher & Converter
def play_music(song_name):
    logger.info(f"Cloud YouTube music requested for: '{song_name}'")
    query = re.sub(r'^(play|listen to|song|music)\s+', '', song_name, flags=re.IGNORECASE).strip()
    if not query:
        query = song_name.strip()

    slug = sanitize_filename(query)
    target_opus = os.path.join(MUSIC_DIR, f"{slug}.opus")
    base_url = get_base_url()

    # 1. Cached check
    if os.path.exists(target_opus) and os.path.getsize(target_opus) > 20000:
        logger.info(f"Found cached track: {target_opus} ({os.path.getsize(target_opus)} bytes)")
        audio_url = f"{base_url}/music/{urllib.parse.quote(f'{slug}.opus')}"
        return json.dumps({
            "status": "ready",
            "title": query.title(),
            "audio_url": audio_url,
            "instructions": f"The song '{query.title()}' is ready. Call tool `self.audio_speaker.play_url` with url='{audio_url}' and title='{query.title()}' immediately to play it on the device speaker."
        }, ensure_ascii=False)

    display_title = query.title()
    temp_raw = os.path.join(MUSIC_DIR, f"temp_{slug}.opus")

    try:
        success = download_youtube_audio(query, temp_raw)
        if not success or not os.path.exists(temp_raw) or os.path.getsize(temp_raw) < 10000:
            return json.dumps({
                "status": "error",
                "message": f"Could not find or download '{query}' from YouTube."
            }, ensure_ascii=False)

        # Convert downloaded YouTube audio to mono 24kHz Opus for ESP32
        logger.info(f"Encoding YouTube audio '{temp_raw}' to mono 24kHz Opus...")
        conv_cmd = [
            "ffmpeg", "-y",
            "-i", temp_raw,
            "-ac", "1",
            "-c:a", "libopus",
            "-ar", "24000",
            "-b:a", "64k",
            target_opus
        ]
        conv_res = subprocess.run(conv_cmd, capture_output=True, text=True, timeout=35)
        
        if os.path.exists(temp_raw):
            try:
                os.remove(temp_raw)
            except Exception:
                pass

        if conv_res.returncode != 0 or not os.path.exists(target_opus) or os.path.getsize(target_opus) < 20000:
            logger.error(f"ffmpeg conversion failed: {conv_res.stderr}")
            return json.dumps({
                "status": "error",
                "message": f"Failed to encode YouTube audio for '{query}'."
            }, ensure_ascii=False)

        logger.info(f"YouTube track ready: '{target_opus}' ({os.path.getsize(target_opus)} bytes)!")
        audio_url = f"{base_url}/music/{urllib.parse.quote(f'{slug}.opus')}"
        return json.dumps({
            "status": "ready",
            "title": display_title,
            "audio_url": audio_url,
            "instructions": f"The song '{display_title}' has been downloaded from YouTube and is ready to stream. Call tool `self.audio_speaker.play_url` with url='{audio_url}' and title='{display_title}' immediately to start playback."
        }, ensure_ascii=False)

    except subprocess.TimeoutExpired:
        logger.error(f"Timed out fetching music for '{query}' from YouTube")
        return json.dumps({"status": "error", "message": "YouTube download timed out. Please try again."})
    except Exception as e:
        logger.error(f"Exception during play_music: {e}")
        return json.dumps({"status": "error", "message": str(e)})

TOOLS_DEFINITIONS = [
    {
        "name": "web_search",
        "description": "Search the live internet using DuckDuckGo. Use this tool whenever the user asks for current facts, latest news, weather forecasts, sport scores, stock prices, or up-to-date information. Unlimited and free.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query (e.g. 'latest space mission news', 'current weather in New York')"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "play_music",
        "description": "Search the unlimited global music library for any requested song, track, or artist in the world and prepare it for streaming. When this tool returns an audio_url, call the device tool `self.audio_speaker.play_url` with that url to start audio playback.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "song_name": {
                    "type": "string",
                    "description": "The title of the song, track, or artist (e.g. 'Let It Happen by Tame Impala', 'Bohemian Rhapsody', 'Lofi hip hop beats')"
                }
            },
            "required": ["song_name"]
        }
    }
]

async def run_mcp_client():
    while True:
        try:
            logger.info(f"Connecting to XiaoZhi Cloud Broker ({MCP_WS_URL[:45]}...)...")
            async with websockets.connect(MCP_WS_URL, ping_interval=20, ping_timeout=20) as ws:
                logger.info("Successfully connected to XiaoZhi Cloud Broker! Status: ONLINE")
                
                async for raw_message in ws:
                    try:
                        msg = json.loads(raw_message)
                        method = msg.get("method")
                        msg_id = msg.get("id")

                        if method == "initialize":
                            resp = {
                                "jsonrpc": "2.0",
                                "id": msg_id,
                                "result": {
                                    "protocolVersion": "2024-11-05",
                                    "capabilities": {
                                        "tools": {}
                                    },
                                    "serverInfo": {
                                        "name": "xiaozhi-cloud-mcp",
                                        "version": "1.0.0"
                                    }
                                }
                            }
                            await ws.send(json.dumps(resp))
                            logger.info("Handshake initialized.")

                        elif method == "notifications/initialized":
                            pass

                        elif method == "tools/list":
                            resp = {
                                "jsonrpc": "2.0",
                                "id": msg_id,
                                "result": {
                                    "tools": TOOLS_DEFINITIONS
                                }
                            }
                            await ws.send(json.dumps(resp))
                            logger.info(f"Registered {len(TOOLS_DEFINITIONS)} tools (web_search, play_music).")

                        elif method == "ping":
                            resp = {
                                "jsonrpc": "2.0",
                                "id": msg_id,
                                "result": {}
                            }
                            await ws.send(json.dumps(resp))

                        elif method == "tools/call":
                            params = msg.get("params", {})
                            tool_name = params.get("name")
                            arguments = params.get("arguments", {})
                            logger.info(f"Tool call requested: {tool_name} with args: {arguments}")
                            
                            content_text = ""
                            if tool_name == "web_search":
                                query = arguments.get("query", "")
                                content_text = duckduckgo_search(query)
                            elif tool_name == "play_music":
                                song = arguments.get("song_name", "")
                                content_text = play_music(song)
                            else:
                                content_text = f"Unknown tool: {tool_name}"

                            resp = {
                                "jsonrpc": "2.0",
                                "id": msg_id,
                                "result": {
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": content_text
                                        }
                                    ],
                                    "isError": False
                                }
                            }
                            await ws.send(json.dumps(resp))
                            logger.info(f"Tool {tool_name} executed successfully.")

                    except Exception as e:
                        logger.error(f"Error handling message: {e}")

        except Exception as e:
            logger.warning(f"Connection lost or failed ({e}). Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    start_http_server()
    t_keepalive = threading.Thread(target=keepalive_worker, daemon=True)
    t_keepalive.start()
    asyncio.run(run_mcp_client())
