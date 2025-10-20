import asyncio
import websockets

# 프록시 서버 경로 (GPU 직접 연결 대신)
SERVER_URL = "ws://49.50.133.91:8000/ws"
WAV_PATH = "/root/wave-app/media/short_sample.wav"

async def test_whisper():
    async with websockets.connect(SERVER_URL, max_size=50_000_000) as ws:
        print("🎧 WAV 파일 전송 중...")
        with open(WAV_PATH, "rb") as f:
            await ws.send(f.read())

        print("📩 Whisper 응답 대기 중...")
        result = await ws.recv()
        print("📝 Whisper 결과:\n", result)

asyncio.run(test_whisper())
