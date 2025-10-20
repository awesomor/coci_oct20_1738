import os
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Header, HTTPException, Response, WebSocket
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

import asyncio
import websockets  # Proxy를 위한 WebSocket 클라이언트

# ==============================
# 설정
# ==============================
APP_PORT = int(os.getenv("PORT", "8000"))
DEFAULT_AUDIO_PATH = Path("media/sample.wav")
AUDIO_FILE = Path(os.getenv("AUDIO_FILE", str(DEFAULT_AUDIO_PATH))).resolve()

# WHISPER_WS_URL = "ws://114.110.135.253:5001/ws"  # Whisper 서버 WebSocket 주소
# 🔁 여기 변경!
WHISPER_WS_URL = "ws://127.0.0.1:5001/ws"



# ==============================
# FastAPI 초기화
# ==============================
app = FastAPI(title="Waveform Player + Whisper Proxy")
templates = Jinja2Templates(directory="templates")

# 정적 파일 (CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

# 오디오 파일 (media)
app.mount("/media", StaticFiles(directory="media"), name="media")

# ==============================
# 오디오 RANGE 스트리밍
# ==============================
def open_range_file(path: Path, range_header: Optional[str], chunk_size: int = 1024 * 1024):
    file_size = path.stat().st_size
    start = 0
    end = file_size - 1

    if range_header:
        units, _, range_val = range_header.partition("=")
        if units == "bytes":
            start_str, _, end_str = range_val.partition("-")
            if start_str.isdigit():
                start = int(start_str)
            if end_str.isdigit():
                end = int(end_str)

    def file_generator():
        with path.open("rb") as f:
            f.seek(start)
            remaining = file_size - start
            while remaining > 0:
                read_size = min(chunk_size, remaining)
                data = f.read(read_size)
                if not data:
                    break
                remaining -= len(data)
                yield data

    return start, end, file_size, file_generator()


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/audio")
def get_audio(range: Optional[str] = Header(None)):
    if not AUDIO_FILE.exists():
        raise HTTPException(status_code=404, detail="Audio file not found.")

    start, end, size, gen = open_range_file(AUDIO_FILE, range)
    mime_type, _ = mimetypes.guess_type(str(AUDIO_FILE))
    mime_type = mime_type or "audio/wav"

    headers = {
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(end - start + 1)
    }

    return StreamingResponse(gen, status_code=206, headers=headers, media_type=mime_type)


# ==============================
# Proxy WebSocket: /ws-proxy
# ==============================
@app.websocket("/ws-proxy")
async def websocket_proxy(client_ws: WebSocket):
    """
    Proxy: Browser <-> FastAPI <-> Whisper STT(WebSocket)
    """
    await client_ws.accept()

    try:
        # 1️⃣ FastAPI → Whisper WebSocket 연결
        async with websockets.connect(WHISPER_WS_URL, max_size=50_000_000) as whisper_ws:
            print("✅ Proxy connected to Whisper server")

            while True:
                # 2️⃣ Browser → WAV bytes 수신
                data = await client_ws.receive_bytes()

                # 3️⃣ Whisper로 전송
                await whisper_ws.send(data)

                # 4️⃣ Whisper 응답 수신
                result = await whisper_ws.recv()

                # 5️⃣ Browser로 응답 전달
                await client_ws.send_text(result)

    except Exception as e:
        print(f"❌ Proxy Error: {e}")
        await client_ws.close()

