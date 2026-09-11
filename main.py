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

# Configuration from Environment Variables
MCP_WS_URL = os.environ.get(
    "MCP_WS_URL",
    "wss://api.xiaozhi.me/mcp/?token=eyJhbGciOiJFUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VySWQiOjEwNjg0MjYsImFnZW50SWQiOjIzMjcxMjEsImVuZHBvaW50SWQiOiJhZ2VudF8yMzI3MTIxIiwicHVycG9zZSI6Im1jcC1lbmRwb2ludCIsImlhdCI6MTc4OTEzMTcyMSwiZXhwIjoxODIwNjg5MzIxfQ.AwQdK_IQkQqUJjsq8tcxOgFcJ8q3Nes2zgR-iMSIByt2B8BKTgXKLAo5vQ0f7cPPLhBk-KJ7rmYI8kpnGakWNQ"
)
PORT = int(os.environ.get("PORT", 10000))
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
MUSIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "music")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("XiaoZhiCloudMCP")

os.makedirs(MUSIC_DIR, exist_ok=True)

def get_base_url():
    if RENDER_EXTERNAL_URL:
        return RENDER_EXTERNAL_URL
    return f"http://localhost:{PORT}"

# HTTP Server for Health Checks and Audio Streaming
class CloudHTTPHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=MUSIC_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/" or self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            resp = json.dumps({
                "status": "online",
                "service": "XiaoZhi Cloud MCP",
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
    logger.info(f"Cloud HTTP Server running on port {PORT}. Public Base URL: {get_base_url()}")

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

# YouTube Cloud Music Fetcher & Converter
def play_music(song_name):
    logger.info(f"Cloud music requested for: '{song_name}'")
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

    # 2. Download from YouTube & convert
    temp_raw = os.path.join(MUSIC_DIR, f"temp_{slug}.opus")
    try:
        logger.info(f"Downloading '{query}' from YouTube...")
        dl_cmd = [
            "python", "-m", "yt_dlp",
            "--default-search", "ytsearch1",
            "--no-playlist",
            "-x", "--audio-format", "opus",
            "--extractor-args", "youtube:player_client=android,web",
            "-o", temp_raw,
            f"ytsearch1:{query}"
        ]
        res = subprocess.run(dl_cmd, capture_output=True, text=True, timeout=40)
        if res.returncode != 0:
            logger.warning(f"Primary yt-dlp attempt failed ({res.stderr[:200] if res.stderr else 'no err'}), trying fallback client...")
            dl_cmd_fallback = [
                "python", "-m", "yt_dlp",
                "--default-search", "ytsearch1",
                "--no-playlist",
                "-x", "--audio-format", "opus",
                "--extractor-args", "youtube:player_client=ios,mweb",
                "-o", temp_raw,
                f"ytsearch1:{query}"
            ]
            res = subprocess.run(dl_cmd_fallback, capture_output=True, text=True, timeout=40)

        if res.returncode != 0:
            logger.error(f"yt-dlp error: {res.stderr}")
            return json.dumps({
                "status": "error",
                "message": f"Could not find or download music for '{query}'."
            }, ensure_ascii=False)

        # Convert to mono 24kHz Opus for ESP32
        logger.info(f"Encoding '{temp_raw}' to mono 24kHz Opus...")
        conv_cmd = [
            "ffmpeg", "-y",
            "-i", temp_raw,
            "-ac", "1",
            "-c:a", "libopus",
            "-ar", "24000",
            "-b:a", "64k",
            target_opus
        ]
        conv_res = subprocess.run(conv_cmd, capture_output=True, text=True, timeout=20)
        
        if os.path.exists(temp_raw):
            try:
                os.remove(temp_raw)
            except Exception:
                pass

        if conv_res.returncode != 0 or not os.path.exists(target_opus):
            logger.error(f"ffmpeg conversion failed: {conv_res.stderr}")
            return json.dumps({
                "status": "error",
                "message": f"Failed to encode audio for '{query}'."
            }, ensure_ascii=False)

        logger.info(f"Track ready: '{target_opus}' ({os.path.getsize(target_opus)} bytes)!")
        audio_url = f"{base_url}/music/{urllib.parse.quote(f'{slug}.opus')}"
        return json.dumps({
            "status": "ready",
            "title": query.title(),
            "audio_url": audio_url,
            "instructions": f"The song '{query.title()}' has been downloaded and is ready to stream. Call tool `self.audio_speaker.play_url` with url='{audio_url}' and title='{query.title()}' immediately to start playback."
        }, ensure_ascii=False)

    except subprocess.TimeoutExpired:
        logger.error(f"Timed out fetching music for '{query}'")
        return json.dumps({"status": "error", "message": "Download timed out. Please try again."})
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
