import argparse, serial, asyncio, websockets, json, cv2
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc import RTCConfiguration, RTCIceServer
from av import VideoFrame
from aiortc.contrib.media import MediaPlayer
import fractions
from datetime import datetime
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp
from aiortc.codecs import get_capabilities
from utils import signaling_loop, uart_reader, heartbeat_task, send_periodic
from config import *

# cái này để ép server dùng codec h264
video_caps = get_capabilities("video")
h264_codecs = [c for c in video_caps.codecs if c.mimeType.lower() == "video/h264"]
role = "publisher"

# ====== Camera IP ======
rtsp_url = "rtsp://192.168.0.101:8080/h264.sdp"
# rtsp_url = "rtsp://admin:123456!Vht@192.168.1.120:18554/h264"

async def run(COM_port: str, baudrate: int, timeout: int):
    pc = None
    player = None
    try:
        # kiểm tra RTSP camera bằng opencv trước
        # cap = cv2.VideoCapture(rtsp_url)
        # if not cap.isOpened():
        #     print("RTSP camera not available. Try again ...")
        #     return "no_camera"
        # cap.release()

        # Tùy chọn FFmpeg để ổn định & giảm trễ
        # - rtsp_transport: "tcp" (ổn định) hoặc "udp" (độ trễ thấp hơn nếu mạng tốt)
        # - stimeout: timeout socket (microseconds)
        # - fflags=nobuffer/flags=low_delay: giảm đệm
        # player = MediaPlayer(
        #     rtsp_url,
        #     format="rtsp",
        #     options={
        #         "rtsp_transport": "udp",
        #         "stimeout": "5000000",
        #         "fflags": "nobuffer",
        #         "flags": "low_delay",
        #         "max_delay": "0",
        #         "framedrop": "1",
        #     },
        #     decode=False
        # )

        # Tạo peer connection
        pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
        lost_event = asyncio.Event()

        # kiểm tra tình trạng kết nối
        @pc.on("connectionstatechange")
        async def on_state_change():
            print("[Publisher] state:", pc.connectionState)
            if pc.connectionState in ("failed", "disconnected", "closed"):
                print("[Publisher] connection lost -> set lost_event")
                lost_event.set()
                
        @pc.on("iceconnectionstatechange")
        async def on_ice_state():
            print("[Publisher] ice:", pc.iceConnectionState)
            if pc.iceConnectionState in ("failed", "disconnected", "closed"):
                print("[Publisher] ice connection lost -> set lost_event")
                lost_event.set()
                
        @pc.on("icecandidate")
        async def on_icecandidate(candidate):
            if candidate and getattr(on_icecandidate, "ws", None):
                ws = on_icecandidate.ws
                try:
                    await ws.send(json.dumps({
                        "type":"candidate",
                        "candidate": candidate_to_sdp(candidate).split("\n")[0]
                    }))
                except Exception as e:
                    print("Failed to send ICE candidate:", e)

        # thêm track để gửi đi
        # if player.video:
        #     print("hehehehe")
        #     pc.addTrack(player.video)
        #     # Ép server codec h264
        # else:
        #     print("No video track from RTSP!")
        #     return "no_camera"
        # transceiver = pc.getTransceivers()[0]
        # transceiver.setCodecPreferences(h264_codecs)

        # Tạo kênh gửi dữ liệu
        telemetry_channel = pc.createDataChannel("telemetry")

        # TELEMETRY CHANNEL
        @telemetry_channel.on("open")
        def on_open():
            print("Telemetry channel opened")
            asyncio.ensure_future(send_periodic(telemetry_channel))
            # asyncio.ensure_future(uart_reader(telemetry_channel, COM_port, baudrate))

        @telemetry_channel.on("message")
        def on_message(message):
            print(f"Got from subscriber: {message}")
            
        @telemetry_channel.on("close")
        def on_close():
            print("[Publisher] telemetry channel closed")
            lost_event.set()
            
        @pc.on("datachannel")
        def on_datachannel(channel):
            print("Data channel received:", channel.label)

            @channel.on("message")
            def on_message(message):
                if channel.label == "heartbeat_viewer":
                    if message == "ping":
                        # print(f"Got ping from {channel.label} channel, sending pong")
                        try:
                            channel.send(f"pong")
                        except Exception as e:
                            print(f"{channel.label} channel send error: ", e)

                elif channel.label == "gcs_command":
                    print(f"{channel.label} channel got: {message}")
                
            @channel.on("close")
            def on_close():
                print(f"{channel.label} channel closed")
                lost_event.set()
            
        # chạy signaling loop song song
        signaling_task = asyncio.create_task(signaling_loop(pc, lost_event, on_icecandidate, role, timeout, SIGNALING_SERVER))

        # chờ cho tới khi PC mất
        await lost_event.wait()
        signaling_task.cancel()
        print("Peer connection lost -> rebuild peer")
        await pc.close()        # đóng peer cũ
        return "retry"
    
    except Exception as e:
        print("Error:", e)
        return "retry"
    
    finally:
        if pc:
            await pc.close()
        if player:
            player.video.stop()


# ====== main ======
async def main():
    parser = argparse.ArgumentParser(
        description="WebRTC video / data-channels implementation"
    )
    parser.add_argument(
        "--COM_port", default="/dev/ttyACM0"
    )
    parser.add_argument(
        "--baudrate", type=int, default=115200
    )
    parser.add_argument(
        "--timeout", type=int, default=3, help="Signaling server connecting timeout (default: 3s)"
    )
    args = parser.parse_args()

    while True:
        result = await run(args.COM_port, args.baudrate, args.timeout)
        if result in ["retry", "no_camera"]:
            if result == "retry":
                print("Got retry, retrying ...")
            await asyncio.sleep(PC_RETRY_TIME)  # retry sau vài giây
            continue
        else:
            break


if __name__ == "__main__":
    asyncio.run(main())
