# ==============================================
# main.py — Scene-aware API + STT Proxy + Similarity
# ==============================================
import os
import mimetypes
import time
import traceback
from pathlib import Path
from typing import Optional, Dict, Any, List

import requests
from fastapi import FastAPI, Request, Header, HTTPException, UploadFile, File, Body
from fastapi.responses import StreamingResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

# ---------------------------
# Similarity (rapidfuzz preferred, difflib fallback)
# ---------------------------
try:
    from rapidfuzz import process, fuzz  # pip install rapidfuzz
    _HAS_RAPIDFUZZ = True
except Exception:
    _HAS_RAPIDFUZZ = False
    from difflib import SequenceMatcher

# ---------------------------
# Script loader (scene-aware)
# ---------------------------
# script_loader.py 내부에 아래 2개가 있어야 함:
# - load_script_lines()              -> ["문장1", ...]
# - load_script_with_scenes()        -> {"lines":[...], "scenes":[{"scene":n,"line":"..."},...], "scene_count":N}
from script_loader import load_script_lines, load_script_with_scenes


# ==============================
# 환경 설정
# ==============================
APP_PORT = int(os.getenv("PORT", "8000"))

DEFAULT_AUDIO_PATH = Path("media/sample.wav")
AUDIO_FILE = Path(os.getenv("AUDIO_FILE", str(DEFAULT_AUDIO_PATH))).resolve()

# Whisper 서버 HTTP 엔드포인트 (GPU 서버)
WHISPER_HTTP_URL = os.getenv("WHISPER_HTTP_URL", "http://114.110.135.253:5001/stt")

# 업로드 제한 및 타임아웃
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(50_000_000)))  # 50MB
WHISPER_HTTP_TIMEOUT = int(os.getenv("WHISPER_HTTP_TIMEOUT", "90"))     # seconds


# ==============================
# FastAPI 초기화
# ==============================
app = FastAPI(title="Wave Player + RMS Chunking + Whisper (HTTP) + Similarity + Scene API")
templates = Jinja2Templates(directory="templates")

# 정적/미디어
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/media", StaticFiles(directory="media"), name="media")


# ==============================
# 스크립트 로드 (서버 기동 시)
# ==============================
# 호환성: 기존 lines 전용도 유지하면서, scene-aware 구조도 함께 보유
SCRIPT_DATA: Dict[str, Any] = load_script_with_scenes()
SCRIPT_LINES_LIST: List[str] = SCRIPT_DATA.get("lines") or load_script_lines()
SCENE_MAPPED: List[Dict[str, Any]] = SCRIPT_DATA.get("scenes", [])
SCENE_COUNT: int = int(SCRIPT_DATA.get("scene_count", 0))

# 내부 인덱스 → 스크립트 줄 텍스트 매핑 (유사도 대상)
# (기존 로직과 호환: 단순 리스트 형태)
SCRIPT_LINES = SCRIPT_LINES_LIST[:]  # shallow copy


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
        "Content-Length": str(end - start + 1),
    }
    return StreamingResponse(gen, status_code=206, headers=headers, media_type=mime_type)


# ==============================
# HTTP STT 프록시: WAV → Whisper HTTP
# ==============================
@app.post("/stt-proxy")
async def stt_proxy(file: UploadFile = File(...)):
    total_start = time.time()
    try:
        data = await file.read()
        if not data:
            return JSONResponse({"ok": False, "error": "empty file"}, status_code=400)

        if len(data) > MAX_UPLOAD_BYTES:
            return JSONResponse(
                {"ok": False, "error": f"payload too large (> {MAX_UPLOAD_BYTES} bytes)"},
                status_code=413,
            )

        if len(data) < 44:  # WAV header length guard
            return JSONResponse({"ok": False, "error": "invalid wav"}, status_code=400)

        try:
            whisper_start = time.time()
            resp = requests.post(
                WHISPER_HTTP_URL,
                files={"file": ("chunk.wav", data, "audio/wav")},
                timeout=WHISPER_HTTP_TIMEOUT,
            )
            whisper_end = time.time()
        except requests.Timeout:
            return JSONResponse({"ok": False, "error": "whisper_http_timeout"}, status_code=504)
        except requests.RequestException as e:
            return JSONResponse({"ok": False, "error": f"whisper_http_request_failed: {e}"}, status_code=502)

        total_time = round(time.time() - total_start, 3)

        try:
            payload = resp.json()
        except ValueError:
            return JSONResponse({"ok": True, "text": resp.text, "total_s": total_time}, status_code=200)

        stt_time = payload.get("elapsed_s")
        if stt_time is None:
            stt_time = round(whisper_end - whisper_start, 3)
        net_time = round(total_time - float(stt_time), 3)

        return JSONResponse(
            {"ok": True, "text": payload.get("text", ""), "total_s": total_time, "stt_s": float(stt_time), "net_s": net_time},
            status_code=200,
        )

    except Exception:
        traceback.print_exc()
        return JSONResponse({"ok": False, "error": "internal server error"}, status_code=500)


# ==============================
# Similarity (문장 → scripts.txt 최고 유사도)
# ==============================
@app.post("/similar")
async def similar(payload: dict = Body(...)):
    """
    입력 텍스트와 스크립트 비교.
    - 기본: 전체 SCRIPT_LINES 대상
    - 선택: payload["candidates"] = [idx...] 가 있으면 해당 인덱스만 비교
    반환: 최고 유사도(%)와 해당 줄 인덱스(best_idx)
    """
    text = (payload.get("text") or "").strip()
    if not text or not SCRIPT_LINES:
        return {"ok": True, "score_pct": 0.0, "best_idx": None}

    # 후보 제한: [정수 인덱스]가 넘어오면 그 집합만 대상으로 검색
    cand = payload.get("candidates")
    if isinstance(cand, list) and len(cand) > 0:
        # 범위 정리 + 중복 제거 + 정수 변환
        cand_idx = sorted({int(i) for i in cand if isinstance(i, (int, float)) and 0 <= int(i) < len(SCRIPT_LINES)})
        if not cand_idx:
            return {"ok": True, "score_pct": 0.0, "best_idx": None}
        choices = [SCRIPT_LINES[i] for i in cand_idx]
        if _HAS_RAPIDFUZZ:
            from rapidfuzz import process, fuzz
            best = process.extractOne(text, choices, scorer=fuzz.token_set_ratio)
            if best is None:
                return {"ok": True, "score_pct": 0.0, "best_idx": None}
            _, score, local_idx = best
            return {"ok": True, "score_pct": round(float(score), 2), "best_idx": int(cand_idx[local_idx])}
        else:
            from difflib import SequenceMatcher
            def _ratio(a, b): return SequenceMatcher(None, a, b).ratio() * 100.0
            best_score, best_idx = 0.0, None
            for j, s_idx in enumerate(cand_idx):
                s = _ratio(text, SCRIPT_LINES[s_idx])
                if s > best_score:
                    best_score, best_idx = s, s_idx
            return {"ok": True, "score_pct": round(best_score, 2), "best_idx": best_idx}

    # 후보 미지정: 전체 대상
    if _HAS_RAPIDFUZZ:
        from rapidfuzz import process, fuzz
        best = process.extractOne(text, SCRIPT_LINES, scorer=fuzz.token_set_ratio)
        if best is None:
            return {"ok": True, "score_pct": 0.0, "best_idx": None}
        _, score, best_idx = best
        return {"ok": True, "score_pct": round(float(score), 2), "best_idx": int(best_idx)}

    # fallback: difflib
    from difflib import SequenceMatcher
    def _ratio(a, b): return SequenceMatcher(None, a, b).ratio() * 100.0
    best_score, best_idx = 0.0, None
    for i, line in enumerate(SCRIPT_LINES):
        s = _ratio(text, line)
        if s > best_score:
            best_score, best_idx = s, i
    return {"ok": True, "score_pct": round(best_score, 2), "best_idx": best_idx}


# ==============================
# 스크립트 제공 (Scene-aware)
# ==============================
@app.get("/script")
def get_script():
    """
    Scene-aware 구조를 프런트로 전달.
    기존 호환을 위해 lines 배열은 [{idx, text}] 형태로 제공.
    또한 각 줄의 scene 번호를 함께 실어 보낸다.
    """
    lines_payload = []
    if SCENE_MAPPED and len(SCENE_MAPPED) == len(SCRIPT_LINES):
        # scene 정보를 각 줄에 매핑
        for idx, (txt, scene_obj) in enumerate(zip(SCRIPT_LINES, SCENE_MAPPED)):
            lines_payload.append({
                "idx": idx,
                "text": txt,
                "scene": int(scene_obj.get("scene", 1))
            })
    else:
        # scene 정보가 없는 경우(혹은 길이 불일치) — scene=1로 기본화
        for idx, txt in enumerate(SCRIPT_LINES):
            lines_payload.append({
                "idx": idx,
                "text": txt,
                "scene": 1
            })

    return {
        "ok": True,
        "count": len(lines_payload),
        "scene_count": SCENE_COUNT if SCENE_COUNT > 0 else len(set(i["scene"] for i in lines_payload)),
        "lines": lines_payload
    }


# ==============================
# Health
# ==============================
@app.get("/health")
def health():
    return {
        "ok": True,
        "whisper_http_url": WHISPER_HTTP_URL,
        "script_lines": len(SCRIPT_LINES),
        "scene_count": SCENE_COUNT,
    }


# ==============================
# 개발용 실행 (uvicorn)
# ==============================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=APP_PORT, reload=False)
