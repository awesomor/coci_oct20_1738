import os
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Header, HTTPException, Response
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles  # ← 추가

APP_PORT = int(os.getenv("PORT", "8000"))
DEFAULT_AUDIO_PATH = Path("media/sample.wav")
AUDIO_FILE = Path(os.getenv("AUDIO_FILE", str(DEFAULT_AUDIO_PATH))).resolve()

app = FastAPI(title="Waveform Player")
templates = Jinja2Templates(directory="templates")
WHISPER_WS_URL = "ws://114.110.135.253:5001/ws"   # Whisper server URL
# ✅ 정적 파일 서빙 추가
app.mount("/static", StaticFiles(directory="static"), name="static")
# ✅ 오디오 파일(media 폴더) 서빙
app.mount("/media", StaticFiles(directory="media"), name="media")


def open_range_file(path: Path, range_header: Optional[str], chunk_size: int = 1024 * 1024):
    """
    HTTP Range 요청을 처리해서 바이트 범위를 스트리밍.
    """
    file_size = path.stat().st_size
    start = 0
    end = file_size - 1

    if range_header:
        # e.g. "bytes=1000-2000" or "bytes=1000-"
        units, _, range_spec = range_header.partition("=")
        if units.strip().lower() != "bytes":
            raise HTTPException(status_code=416, detail="Invalid unit")
        start_end = range_spec.split("-")
        if start_end[0].strip():
            start = int(start_end[0])
        if len(start_end) > 1 and start_end[1].strip():
            end = int(start_end[1])
        if start > end or start >= file_size:
            raise HTTPException(status_code=416, detail="Invalid range")

    def iter_file():
        with open(path, "rb") as f:
            f.seek(start)
            bytes_remaining = end - start + 1
            while bytes_remaining > 0:
                read_size = min(chunk_size, bytes_remaining)
                data = f.read(read_size)
                if not data:
                    break
                bytes_remaining -= len(data)
                yield data

    content_length = end - start + 1
    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
    }
    status_code = 206 if range_header else 200
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return StreamingResponse(iter_file(), status_code=status_code, media_type=media_type, headers=headers)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if not AUDIO_FILE.exists():
        raise HTTPException(status_code=404, detail=f"Audio file not found: {AUDIO_FILE}")
    # 프론트에서 fetch/decode할 URL
    audio_url = "/audio"
    return templates.TemplateResponse("index.html", {"request": request, "audio_url": audio_url})


@app.get("/audio")
def get_audio(range: Optional[str] = Header(None)):
    if not AUDIO_FILE.exists():
        raise HTTPException(status_code=404, detail=f"Audio file not found: {AUDIO_FILE}")
    return open_range_file(AUDIO_FILE, range_header=range)


# 헬스체크
@app.get("/healthz")
def healthz():
    return {"ok": True, "audio": str(AUDIO_FILE)}

@app.websocket("/ws-proxy")
async def websocket_proxy(client_ws: WebSocket):
    """
    Proxy: Browser <-> FastAPI <-> Whisper STT(WebSocket)
    """
    await client_ws.accept()

    try:
        # 1️⃣ FastAPI 서버에서 Whisper 서버로 WebSocket 연결 (Server → Server)
        async with websockets.connect(WHISPER_WS_URL, max_size=50_000_000) as whisper_ws:
            print("✅ Proxy connected to Whisper server")

            while True:
                # 2️⃣ 브라우저에서 바이너리 수신 (WAV 청크)
                data = await client_ws.receive_bytes()

                # 3️⃣ Whisper 서버로 전송
                await whisper_ws.send(data)

                # 4️⃣ Whisper 응답 수신 (텍스트)
                result = await whisper_ws.recv()

                # 5️⃣ 브라우저로 응답 전달
                await client_ws.send_text(result)

    except Exception as e:
        print(f"❌ Proxy Error: {e}")
        await client_ws.close()
