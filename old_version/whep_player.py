import asyncio
import aiohttp
import cv2
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaBlackhole

# ====== SỬA CHO PHÙ HỢP MÔI TRƯỜNG ======
MEDIAMTX_HOST = "117.5.76.131:8889"  # vd: "my-domain.com:8889" hoặc "203.0.113.10:8889"
STREAM_PATH   = "cam1"                 # path của stream
TURN_HOST     = "117.5.76.131:3478"       # vd: "198.51.100.22:3478"
TURN_USER     = "siuuu"
TURN_PASS     = "1123581321"
SHOW_WINDOW   = True    # True: hiển thị bằng OpenCV; False: chỉ nhận không hiển thị
# ========================================

class VideoSink:
    def __init__(self, show_window=True):
        self.show = show_window

    async def recv(self, track):
        """Nhận frame từ track video và hiển thị."""
        while True:
            frame = await track.recv()
            if self.show:
                img = frame.to_ndarray(format="bgr24")
                cv2.imshow("WebRTC - MediaMTX", img)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break

async def main():
    config = RTCConfiguration(iceServers=[
        RTCIceServer(urls=[f"stun:{TURN_HOST}"]),
        RTCIceServer(urls=[f"turn:{TURN_HOST}"], username=TURN_USER, credential=TURN_PASS),
    ])

    pc = RTCPeerConnection(configuration=config)

    # Nhận video (và audio nếu cần)
    pc.addTransceiver("video", direction="recvonly")
    # pc.addTransceiver("audio", direction="recvonly")

    video_sink = VideoSink(show_window=SHOW_WINDOW)

    @pc.on("track")
    def on_track(track):
        print("Track received:", track.kind)
        if track.kind == "video":
            asyncio.ensure_future(video_sink.recv(track))
        else:
            # Nếu muốn bỏ qua audio
            blackhole = MediaBlackhole()
            asyncio.ensure_future(_consume_audio(track, blackhole))

    # Tạo offer và chờ ICE gathering xong (non-trickle)
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    await _wait_ice_gathering_complete(pc)

    sdp_offer = pc.localDescription.sdp
    whep_url = f"http://{MEDIAMTX_HOST}/{STREAM_PATH}/whep"
    print("POST SDP to:", whep_url)

    async with aiohttp.ClientSession() as session:
        async with session.post(whep_url, data=sdp_offer, headers={"Content-Type": "application/sdp"}) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RuntimeError(f"WHEP POST failed: {resp.status} {resp.reason}\n{text}")
            answer_sdp = await resp.text()

    await pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))
    print("Remote answer set. Streaming... Press Ctrl+C to quit.")

    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await pc.close()
        cv2.destroyAllWindows()

async def _consume_audio(track, sink):
    # đọc audio để giữ session ổn định (nếu cần)
    while True:
        frame = await track.recv()
        await sink.write(frame)

async def _wait_ice_gathering_complete(pc: RTCPeerConnection):
    if pc.iceGatheringState == "complete":
        return
    fut = asyncio.get_event_loop().create_future()

    @pc.on("icegatheringstatechange")
    def _on_state_change():
        if pc.iceGatheringState == "complete" and not fut.done():
            fut.set_result(True)

    await fut

if __name__ == "__main__":
    asyncio.run(main())
