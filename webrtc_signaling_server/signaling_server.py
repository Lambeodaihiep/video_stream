import asyncio
import websockets
import json

# Danh sách kết nối client
publisher = None           # websocket object của publisher
viewers = set()            # list websocket của viewers
pending_offer = None       # lưu offer gần nhất của publisher
pending_candidates = []    # lưu ICE candidates từ publisher trước khi viewer kết nối

async def handler(ws):
    global publisher, pending_offer, pending_candidates
    try:
        # step 1: client phải gửi role ngay sau khi kết nối
        msg = await ws.recv()
        hello = json.loads(msg)
        role = hello.get("role")

        if role == "publisher":
            # luôn reset nếu có publisher cũ
            if publisher is not None:
                try:
                    await publisher.close()
                except:
                    pass
            publisher = ws
            pending_offer = None
            pending_candidates = []
            print("Publisher connected")
            # nhận message từ publisher
            async for msg in ws:
                data = json.loads(msg)
                if data["type"] == "offer":
                    pending_offer = data
                    # gửi cho tất cả viewer đang online
                    for v in viewers:
                        await v.send(json.dumps(data))
                elif data["type"] == "candidate":
                    # lưu ice candidate, chuyển tiếp cho viewers
                    pending_candidates.append(data)
                    for v in viewers:
                        await v.send(json.dumps(data))
                elif data["type"] == "answer":
                    # publisher thường không gửi answer
                    pass
                    
            # xóa luôn pending offer và candidates 
            pending_offer = None
            pending_candidates = []
            #print("pending delete 1")

        elif role == "viewer":
            viewers.add(ws)
            print(f"Viewer connected, total viewers: {len(viewers)}")
            # nếu đã có pub + offer thì gửi ngay
            if pending_offer is not None:
                await ws.send(json.dumps(pending_offer))
                # gửi luôn ice candidate cũ
                for c in pending_candidates:
                    await ws.send(json.dumps(c))
                
            # nhận message từ viewer
            async for msg in ws:
                data = json.loads(msg)
                if data["type"] == "answer":
                    if publisher:
                        await publisher.send(json.dumps(data))
                elif data["type"] == "candidate":
                    if publisher:
                        await publisher.send(json.dumps(data))
                        
            # xóa luôn pending offer và candidates 
            pending_offer = None
            pending_candidates = []
            #print("pending delete 2")

        else:
            print("Unknown role, closing")
            return
        
    except:
        pass

    finally:
        if ws == publisher:
            print("Publisher disconneted")
            publisher = None
            pending_offer = None
            pending_candidates = []
        else:
            viewers.discard(ws)
            print(f"Viewer disconnected, total viewers: {len(viewers)}")

async def main():
    async with websockets.serve(
        handler,
        "0.0.0.0",
        8889,
        ping_interval=1.5,
        ping_timeout=1.5
    ):
        print("Signaling server running on ws://0.0.0.0:8889")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
