import asyncio
import websockets
import json

# Danh sách kết nối client
clients = set()

async def handler(ws):
    clients.add(ws)
    try:
        async for msg in ws:
            data = json.loads(msg)
            # broadcast tới tất cả client khác
            for c in clients:
                if c != ws:
                    await c.send(json.dumps(data))
    finally:
        clients.remove(ws)

async def main():
    async with websockets.serve(handler, "0.0.0.0", 8889):
        print("Signaling server running on ws://0.0.0.0:8889")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())