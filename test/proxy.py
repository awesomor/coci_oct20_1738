# proxy.py - Whisper WebSocket Proxy Server
import asyncio
import websockets
from websockets.server import serve

WHISPER_WS_URL = "ws://114.110.135.253:5001/ws"

async def handler(client_ws):
    print("🔗 Client Connected: Forwarding to Whisper Server...")
    try:
        async with websockets.connect(WHISPER_WS_URL, max_size=None) as whisper_ws:

            async def client_to_whisper():
                async for message in client_ws:
                    await whisper_ws.send(message)

            async def whisper_to_client():
                async for message in whisper_ws:
                    await client_ws.send(message)

            await asyncio.gather(client_to_whisper(), whisper_to_client())

    except Exception as e:
        print(f"❌ Proxy Error: {e}")

async def main():
    print("🚀 Proxy Server Running on ws://0.0.0.0:8000/ws")
    async with serve(handler, "0.0.0.0", 8000, max_size=None):
        await asyncio.Future()

if __name__ == "__main__":
    asyncio.run(main())

