# main.py (추가/교체 가능)
import os
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Header, HTTPException, Response, WebSocket, UploadFile, File
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

import asyncio
import websockets  # pip install websockets
import traceback
import time

# ==============================
# 설정
# ==============================
APP_PORT = int(os.getenv("PORT", "8000"))
DEFAULT_AUDIO_PATH = Path("media/sample.wav")
AUDIO_FILE = Path(os.getenv("AUDIO_FILE", str(DEFAULT_AUDIO_PATH))).resolve()

# Whisper 서버 WebSocket 주소 — 필요 시 변경
WHISPER_WS_URL = "ws://114.110.135.253:5001/ws"

# 타임아웃 / max size
WHISPER_WS_TIMEOUT = 30  # seconds
WHISPER_MAX_SIZE = 50_000_000

# ==============================
# FastAPI 초기화
# ==============================
app = FastAPI(title="Waveform Player + Whisper Proxy (HTTP)")
templates = Jinja2Templates(directory="templates")

# 정적 파일 (CSS, JS)
app.mount("/static", StaticFiles(directory="static"), name="static")

# 오디오 파일 (media)
app.mount("/media", StaticFiles(directory="media"), name="media")

# ==============================
# 기존 오디오 RANGE 스트리밍 (생략 가능, 기존에 이미 있으면 유지)
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
# HTTP proxy 엔드포인트: 브라우저 -> FastAPI (POST /stt-proxy)
# FastAPI가 내부에서 Whisper WS로 연결해서 처리하고 결과 텍스트 반환
# ==============================
@app.post("/stt-proxy")
async def stt_proxy(file: UploadFile = File(...)):
    """
    클라이언트에서 WAV Blob을 multipart/form-data로 POST하면
    서버에서 Whisper WS에 연결해 바이너리를 전송하고 텍스트 응답을 받아 클라이언트에 JSON으로 돌려줍니다.
    """
    start_time = time.time()
    try:
        # 파일 읽기 (메모리에 올림 — 청크 파일이 크면 임시 파일 방식으로 바꿀 것)
        data = await file.read()
        if not data:
            return JSONResponse({"ok": False, "error": "empty file"}, status_code=400)

        # 서버에서 Whisper WS로 연결 시도
        # NOTE: 서버에서 Whisper WS 연결이 차단되어 있다면 여기서 예외 발생
        try:
            async with websockets.connect(
                WHISPER_WS_URL,
                max_size=WHISPER_MAX_SIZE,
                ping_interval=10,
                ping_timeout=20
            ) as ws:
                # 전송
                await ws.send(data)
                # 수신 (타임아웃 처리)
                try:
                    resp = await asyncio.wait_for(ws.recv(), timeout=WHISPER_WS_TIMEOUT)
                except asyncio.TimeoutError:
                    return JSONResponse({"ok": False, "error": "whisper response timeout"}, status_code=504)

                elapsed = time.time() - start_time
                return JSONResponse({"ok": True, "text": resp, "elapsed_s": elapsed})
        except Exception as e:
            # 명확한 로그를 남기고 클라이언트에 에러 반환
            tb = traceback.format_exc()
            print("❌ stt_proxy -> whisper ws error:", e)
            print(tb)
            return JSONResponse({"ok": False, "error": f"proxy->whisper-ws-failed: {str(e)}"}, status_code=502)

    except Exception as e:
        tb = traceback.format_exc()
        print("❌ stt_proxy unexpected error:", e)
        print(tb)
        return JSONResponse({"ok": False, "error": "internal server error"}, status_code=500)


# ==============================
# (선택) 간단한 health 체크
# ==============================
@app.get("/health")
def health():
    return {"ok": True}
