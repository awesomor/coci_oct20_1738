import os
import io
import json
import base64
import logging
import asyncio
import tempfile
from typing import Optional

import websockets
from fastapi import FastAPI, UploadFile, File, Body, Request, HTTPException
from fastapi.responses import JSONResponse

# ---------------------------
# Config
# ---------------------------
WHISPER_WS_URL = os.getenv("WHISPER_WS_URL", "ws://114.110.135.253:5001/ws")
WS_CONNECT_TIMEOUT = float(os.getenv("WS_CONNECT_TIMEOUT", "15"))  # sec
WS_MAX_MSG_SIZE = int(os.getenv("WS_MAX_MSG_SIZE", str(50_000_000)))  # 50MB

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("stt_proxy")

app = FastAPI(title="WAV → Whisper WS Proxy", version="1.0.0")


# ---------------------------
# Utilities
# ---------------------------
async def send_wav_to_whisper_ws(wav_bytes: bytes) -> str:
    """
    완성된 WAV bytes를 Whisper WebSocket에 전송하고 단일 응답을 문자열로 반환.
    Whisper 응답이 JSON 문자열이면 text 필드를, 아니면 원문 문자열을 반환.
    """
    try:
        logger.info(f"Connecting WS -> {WHISPER_WS_URL}")
        async with websockets.connect(
            WHISPER_WS_URL,
            max_size=WS_MAX_MSG_SIZE,
            ping_interval=None,
            close_timeout=5,
            open_timeout=WS_CONNECT_TIMEOUT,
        ) as ws:
            await ws.send(wav_bytes)
            logger.info("WAV sent. Awaiting result...")
            result = await asyncio.wait_for(ws.recv(), timeout=120)  # 2분까지 대기
    except asyncio.TimeoutError:
        logger.exception("WS timeout while waiting for response")
        raise HTTPException(status_code=504, detail="Whisper WS timeout (no response).")
    except Exception as e:
        logger.exception("WS error")
        raise HTTPException(status_code=502, detail=f"Whisper WS error: {e}")

    # 응답이 JSON이면 text/segments 등에서 텍스트를 추출하고, 아니면 그대로 반환
    text = None
    try:
        obj = json.loads(result)
        # 흔한 케이스 커버: {"text": "..."} 또는 {"segments":[{"text":"..."}, ...]}
        if isinstance(obj, dict):
            if "text" in obj and isinstance(obj["text"], str):
                text = obj["text"]
            elif "segments" in obj and isinstance(obj["segments"], list):
                text = "".join(seg.get("text", "") for seg in obj["segments"])
    except Exception:
        pass

    return text if text is not None else (result if isinstance(result, str) else str(result))


def is_wav_header(data: bytes) -> bool:
    # 최소한의 WAV 헤더 검증 (RIFF....WAVE)
    return len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WAVE"


# ---------------------------
# Endpoints
# ---------------------------

@app.get("/")
def root():
    return {"message": "WAV → Whisper WS Proxy ready", "ws_url": WHISPER_WS_URL}


@app.post("/stt-proxy", summary="완성 WAV 1개 전송 → Whisper 응답 텍스트 반환")
async def stt_proxy(
    request: Request,
    audio: Optional[UploadFile] = File(default=None, description="multipart/form-data: field name 'audio'"),
    wav_b64: Optional[str] = Body(default=None, description="application/json: { 'wav_b64': '...'}")
):
    """
    지원 입력:
    1) multipart/form-data: 'audio' 필드에 WAV 파일 업로드
    2) application/json: {"wav_b64": "<base64 wav>"}
    3) application/octet-stream: 요청 바디에 RAW WAV bytes
    """
    content_type = request.headers.get("content-type", "")

    # 1) multipart/form-data
    if audio is not None:
        wav_bytes = await audio.read()
        source = "multipart"
    # 2) application/json
    elif wav_b64 is not None:
        try:
            wav_bytes = base64.b64decode(wav_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 in 'wav_b64'.")
        source = "json_base64"
    # 3) application/octet-stream (raw)
    elif "application/octet-stream" in content_type.lower():
        wav_bytes = await request.body()
        source = "octet-stream"
    else:
        raise HTTPException(
            status_code=415,
            detail="Unsupported content-type. Use multipart/form-data (audio), application/json (wav_b64), or application/octet-stream."
        )

    if not wav_bytes or len(wav_bytes) < 44:
        raise HTTPException(status_code=400, detail="Empty or too small WAV payload.")

    if not is_wav_header(wav_bytes):
        # 필요 시 여기서 PCM→WAV 래핑 로직을 넣을 수 있으나, 현재 정책: 완성 WAV만 허용
        raise HTTPException(status_code=400, detail="Input must be a complete WAV file (RIFF/WAVE header missing).")

    logger.info(f"Received WAV ({len(wav_bytes)} bytes) via {source}. Forwarding to Whisper WS...")

    text = await send_wav_to_whisper_ws(wav_bytes)

    return JSONResponse({"ok": True, "source": source, "text": text})
