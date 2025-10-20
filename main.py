# main.py
import os
import mimetypes
import time
import traceback
from pathlib import Path
from typing import Optional
import websocket # 'websocket-client' 라이브러리가 설치되어 있어야 합니다.

import requests
from fastapi import FastAPI, Request, Header, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

# ==============================
# 설정
# ==============================
APP_PORT = int(os.getenv("PORT", "8000"))

DEFAULT_AUDIO_PATH = Path("media/sample.wav")
AUDIO_FILE = Path(os.getenv("AUDIO_FILE", str(DEFAULT_AUDIO_PATH))).resolve()

# Whisper 서버 HTTP 엔드포인트 (GPU 서버에 /stt 추가되어 있어야 함)
#WHISPER_HTTP_URL = os.getenv("WHISPER_HTTP_URL", "http://114.110.135.253:5001/stt")
WHISPER_HTTP_URL = os.getenv("WHISPER_HTTP_URL", "ws://114.110.135.253:5001/ws")

# 업로드 제한 및 타임아웃
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(50_000_000)))  # 50MB
WHISPER_HTTP_TIMEOUT = int(os.getenv("WHISPER_HTTP_TIMEOUT", "90"))     # seconds

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
# 기존 오디오 RANGE 스트리밍
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
        "Content-Length": str(end - start + 1),
    }
    return StreamingResponse(gen, status_code=206, headers=headers, media_type=mime_type)


# ==============================
# HTTP proxy 엔드포인트:
# 브라우저 -> (POST /stt-proxy) -> CPU 서버 -> (HTTP POST /stt) -> Whisper 서버
# ==============================
@app.post("/stt-proxy")
async def stt_proxy(file: UploadFile = File(...)):
    """
    클라이언트에서 WAV Blob을 multipart/form-data로 POST하면
    CPU 서버가 Whisper HTTP(/stt)에 업로드하고 결과(JSON)를 그대로 반환.
    Response JSON: { ok: True/False, text?: str, error?: str, elapsed_s?: float }
    """
    t0 = time.time()
    try:
        # 파일 읽기
        data = await file.read()
        if not data:
            return JSONResponse({"ok": False, "error": "empty file"}, status_code=400)

        if len(data) > MAX_UPLOAD_BYTES:
            return JSONResponse(
                {"ok": False, "error": f"payload too large (> {MAX_UPLOAD_BYTES} bytes)"},
                status_code=413,
            )

        # (선택) 매우 간단한 WAV 헤더 체크 (RIFF/WAVE)
        if len(data) >= 12 and not (data[0:4] == b"RIFF" and data[8:12] == b"WAVE"):
            # WAV가 아닐 수 있으니 경고만 하고 진행할 수도 있음. 여기선 400으로 반환.
            return JSONResponse({"ok": False, "error": "not a valid WAV file (RIFF/WAVE missing)"}, status_code=400)

        '''
        # Whisper 서버로 HTTP POST
        try:
            resp = requests.post(
                WHISPER_HTTP_URL,
                files={"file": ("audio.wav", data, "audio/wav")},
                timeout=WHISPER_HTTP_TIMEOUT,
            )
        except requests.Timeout:
            return JSONResponse({"ok": False, "error": "whisper_http_timeout"}, status_code=504)
        except requests.RequestException as e:
            return JSONResponse({"ok": False, "error": f"whisper_http_request_failed: {e}"}, status_code=502)
        '''


        # Whisper 서버로 WebSockets 연결 및 전송
        try:
            # 1. WebSockets 연결
            ws = websocket.create_connection(WHISPER_HTTP_URL, timeout=WHISPER_HTTP_TIMEOUT)

            # 2. 오디오 데이터 전송 (WebSockets 방식에 맞게 데이터 인코딩 필요)
            #    *주의: WebSockets API가 데이터를 어떤 형식으로 원하는지에 따라 이 부분은 달라집니다.*
            ws.send_binary(data)

            # 3. 응답 수신
            result = ws.recv()
            ws.close()

            # 4. JSON 응답 처리 (수신된 result를 JSON으로 파싱해야 함)
            # resp = JSON.parse(result) ... 와 같은 추가 처리 필요

            # 예시: 임시 응답 처리 (실제 환경에 맞게 수정 필요)
            # return JSONResponse({"ok": True, "result": result}, status_code=200)

            # (이 부분은 기존 try/except 블록 구조를 유지하기 위해 생략)





            # 5. JSON 응답 처리
            try:
                # 수신된 문자열 응답을 JSON 객체로 파싱합니다.
                resp_json = json.loads(result)
                # 성공 시 응답의 일부를 로그로 남깁니다. (예: 'ok' 필드)
                print(f"SUCCESS: [STT Proxy] 5. 응답 JSON 파싱 완료. (OK: {resp_json.get('ok')}, 결과 일부: {str(resp_json)[:100]}...")
        
                # TODO: 여기서 최종 JSONResponse를 반환하는 코드를 작성해야 합니다.
                # return JSONResponse(resp_json, status_code=200)

            except json.JSONDecodeError:
                print(f"ERROR: [STT Proxy] 5. 응답 JSON 파싱 실패. 수신된 원본 데이터: {result}")
                # JSON 파싱 실패 시, 원본 데이터를 에러 메시지에 포함하여 반환합니다.
                return JSONResponse({"ok": False, "error": f"whisper_ws_response_invalid_json: {result[:200]}..."}, status_code=500)









        except websocket.WebSocketTimeoutException:
            return JSONResponse({"ok": False, "error": "whisper_ws_timeout"}, status_code=504)
        except Exception as e: # 웹소켓 관련 일반 오류 처리
            return JSONResponse({"ok": False, "error": f"whisper_ws_request_failed: {e}"}, status_code=502)



        elapsed = time.time() - t0

        # Whisper 응답 처리
        if not resp.ok:
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"whisper_http_failed {resp.status_code}",
                    "detail": resp.text[:1000],
                    "elapsed_s": round(elapsed, 3),
                },
                status_code=502,
            )

        # 정상 JSON 기대: { ok: True, text: "..." }
        try:
            payload = resp.json()
        except ValueError:
            # Whisper가 text/plain 등으로 보냈을 경우 처리
            return JSONResponse(
                {"ok": True, "text": resp.text, "elapsed_s": round(elapsed, 3)},
                status_code=200,
            )

        # 형식 정규화
        if isinstance(payload, dict):
            payload.setdefault("ok", True)
            payload["elapsed_s"] = round(elapsed, 3)
            return JSONResponse(payload, status_code=200)

        # dict가 아니면 문자열 등으로 간주
        return JSONResponse({"ok": True, "text": str(payload), "elapsed_s": round(elapsed, 3)}, status_code=200)

    except Exception as e:
        tb = traceback.format_exc()
        print("❌ stt_proxy unexpected error:", e)
        print(tb)
        return JSONResponse({"ok": False, "error": "internal server error"}, status_code=500)


# ==============================
# Health 체크
# ==============================
@app.get("/health")
def health():
    return {"ok": True, "whisper_http_url": WHISPER_HTTP_URL}
