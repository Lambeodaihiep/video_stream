# signaling_server_pro.py
import os
import asyncio
import json
import logging
from typing import Dict, Set, Optional
from urllib.parse import urlparse, parse_qs

import websockets
from websockets.server import WebSocketServerProtocol

# ------------------ Config ------------------
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8889"))
ALLOWED_ORIGINS = {o.strip() for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()}  # e.g. "https://example.com,https://app.example.com"
AUTH_TOKEN = os.getenv("AUTH_TOKEN")  # optional static token. Set e.g. to "supersecret" and pass ?token=supersecret
MAX_MSGS_PER_SEC = float(os.getenv("MAX_MSGS_PER_SEC", "50"))
ROOM_CAP = int(os.getenv("ROOM_CAP", "64"))
PING_INTERVAL = float(os.getenv("PING_INTERVAL", "10"))
PING_TIMEOUT = float(os.getenv("PING_TIMEOUT", "5"))

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("signaling")

# ------------------ Data model ------------------
class Room:
    def __init__(self, name: str):
        self.name = name
        self.peers: Dict[str, WebSocketServerProtocol] = {}  # peer_id -> ws

    def list_peers(self) -> Set[str]:
        return set(self.peers.keys())

rooms: Dict[str, Room] = {}
peer_room: Dict[WebSocketServerProtocol, str] = {}  # ws -> room_name
peer_id_of: Dict[WebSocketServerProtocol, str] = {}  # ws -> peer_id

# ------------------ Rate limiter (leaky bucket) ------------------
class RateLimiter:
    def __init__(self, rate_per_sec: float, burst: Optional[int] = None):
        self.rate = rate_per_sec
        self.capacity = burst or max(10, int(rate_per_sec * 2))
        self.tokens = self.capacity
        self.updated = asyncio.get_event_loop().time()

    def allow(self, cost: int = 1) -> bool:
        now = asyncio.get_event_loop().time()
        elapsed = now - self.updated
        self.updated = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        if self.tokens >= cost:
            self.tokens -= cost
            return True
        return False

limiters: Dict[WebSocketServerProtocol, RateLimiter] = {}

# ------------------ Utils ------------------
async def send_json(ws: WebSocketServerProtocol, obj: dict):
    try:
        await ws.send(json.dumps(obj))
    except Exception as e:
        log.debug(f"send failed: {e}")

def parse_query(path: str):
    u = urlparse(path)
    q = parse_qs(u.query)
    room = (q.get("room", [None])[0] or "").strip()
    peer = (q.get("peer", [None])[0] or "").strip()
    token = (q.get("token", [None])[0] or "").strip()
    return room, peer, token

def origin_allowed(ws: WebSocketServerProtocol) -> bool:
    if not ALLOWED_ORIGINS:
        return True
    origin = ws.request_headers.get("Origin")
    return origin in ALLOWED_ORIGINS

async def join_room(ws: WebSocketServerProtocol, room_name: str, peer_id: str):
    room = rooms.get(room_name)
    if room is None:
        room = rooms[room_name] = Room(room_name)
    if len(room.peers) >= ROOM_CAP and peer_id not in room.peers:
        await send_json(ws, {"type": "error", "reason": "room-full"})
        await ws.close(code=4000, reason="room-full")
        return False

    # Replace old session if same peer_id exists
    old = room.peers.get(peer_id)
    if old and old is not ws:
        try:
            await send_json(old, {"type": "error", "reason": "replaced-by-new-connection"})
            await old.close(code=4001, reason="duplicate-peer")
        except:
            pass

    room.peers[peer_id] = ws
    peer_room[ws] = room_name
    peer_id_of[ws] = peer_id

    # Notify this peer about existing peers
    other_peers = [p for p in room.peers.keys() if p != peer_id]
    await send_json(ws, {"type": "peers", "room": room_name, "you": peer_id, "peers": other_peers})

    # Notify others
    for pid, other_ws in list(room.peers.items()):
        if pid != peer_id:
            await send_json(other_ws, {"type": "peer-joined", "peer": peer_id})
    log.info(f"{peer_id} joined room '{room_name}' (size={len(room.peers)})")
    return True

async def leave_room(ws: WebSocketServerProtocol):
    room_name = peer_room.pop(ws, None)
    peer_id = peer_id_of.pop(ws, None)
    limiters.pop(ws, None)
    if not room_name or not peer_id:
        return
    room = rooms.get(room_name)
    if not room:
        return
    # Remove
    if room.peers.get(peer_id) is ws:
        room.peers.pop(peer_id, None)
    # Notify others
    for pid, other_ws in list(room.peers.items()):
        await send_json(other_ws, {"type": "peer-left", "peer": peer_id})
    # Cleanup room if empty
    if not room.peers:
        rooms.pop(room_name, None)
    log.info(f"{peer_id} left room '{room_name}' (size={len(room.peers) if room_name in rooms else 0})")

# ------------------ Core handler ------------------
async def handler(ws: WebSocketServerProtocol):
    # Basic origin check
    if not origin_allowed(ws):
        await ws.close(code=4003, reason="origin-not-allowed")
        return

    path = getattr(ws, "path", None) or getattr(getattr(ws, "request", None), "path", "")
    room_name, peer_id, token = parse_query(path or "")
    if not room_name or not peer_id:
        await send_json(ws, {"type": "error", "reason": "missing-room-or-peer"})
        await ws.close(code=4400, reason="bad-query")
        return

    if AUTH_TOKEN and token != AUTH_TOKEN:
        await send_json(ws, {"type": "error", "reason": "auth-failed"})
        await ws.close(code=4401, reason="unauthorized")
        return

    limiters[ws] = RateLimiter(rate_per_sec=MAX_MSGS_PER_SEC)

    # Heartbeat task using WebSocket ping/pong
    async def heartbeat():
        try:
            while True:
                await asyncio.sleep(PING_INTERVAL)
                pong_waiter = await ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=PING_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning(f"Ping timeout: {peer_id}")
            await ws.close(code=1011, reason="ping-timeout")
        except Exception:
            pass

    hb_task = asyncio.create_task(heartbeat())

    # Join room
    if not await join_room(ws, room_name, peer_id):
        hb_task.cancel()
        return

    try:
        async for raw in ws:
            if not limiters[ws].allow():
                await send_json(ws, {"type": "error", "reason": "rate-limit"})
                # Optional: close on persistent flooding
                # await ws.close(code=4408, reason="rate-limit")
                continue

            # Parse JSON
            try:
                data = json.loads(raw)
            except Exception:
                await send_json(ws, {"type": "error", "reason": "bad-json"})
                continue

            msg_type = data.get("type")
            room = rooms.get(peer_room.get(ws, ""))
            me = peer_id_of.get(ws)

            if not room or not me:
                await send_json(ws, {"type": "error", "reason": "not-in-room"})
                continue

            # Route signaling messages
            if msg_type in ("offer", "answer", "candidate", "renegotiate", "ice-restart"):
                target = data.get("to")
                if not target or target == me:
                    await send_json(ws, {"type": "error", "reason": "invalid-target"})
                    continue
                target_ws = room.peers.get(target)
                if not target_ws:
                    await send_json(ws, {"type": "error", "reason": "target-offline"})
                    continue
                # pass-through payload + from
                payload = {k: v for k, v in data.items() if k not in ("type", "to")}
                await send_json(target_ws, {"type": msg_type, "from": me, **payload})

            elif msg_type == "leave":
                await leave_room(ws)
                await ws.close(code=1000, reason="bye")

            elif msg_type == "peers":
                await send_json(ws, {"type": "peers", "room": room.name, "you": me, "peers": [p for p in room.peers if p != me]})

            elif msg_type == "ping":
                await send_json(ws, {"type": "pong"})

            else:
                # Unknown message
                await send_json(ws, {"type": "error", "reason": "unknown-type", "got": msg_type})

    except websockets.exceptions.ConnectionClosedOK:
        pass
    except websockets.exceptions.ConnectionClosedError:
        pass
    except Exception as e:
        log.exception(f"Handler error: {e}")
    finally:
        hb_task.cancel()
        await leave_room(ws)

# ------------------ Server bootstrap ------------------
async def main():
    log.info(f"Signaling server starting on ws://{HOST}:{PORT}")
    # If you terminate TLS at Python: provide ssl=ssl_context here.
    async with websockets.serve(
        handler,
        HOST,
        PORT,
        # origins enforces Origin header (basic CSRF-ish protection for WS)
        origins=ALLOWED_ORIGINS if ALLOWED_ORIGINS else None,
        max_size=2 * 1024 * 1024,  # 2MB frames (tùy chỉnh)
        ping_interval=None,  # we run our own heartbeat task
    ):
        await asyncio.Future()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down...")
