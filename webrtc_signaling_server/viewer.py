import argparse, socket, asyncio, websockets, json, cv2
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc import RTCConfiguration, RTCIceServer
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp
from utils import (signaling_loop,
                   opencv_display,
                   send_video_packet_udp,
                   heartbeat_task,
                   send_command_from_gcs,
                   opencv_to_gstreamer,
                   signaling_loop_pro)
from config import *

# ====== GCS PORT ======
VIDEO_PORT = 5001
TELEMETRY_PORT = 14450

role = "viewer"

async def run(GCS_IP: str, timeout: int):
    pc = None
    try:
        pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
        lost_event = asyncio.Event()        # gọi set() khi cần retry

        # kiểm tra tình trạng kết nối
        @pc.on("connectionstatechange")
        async def on_state_change():
            print("[Viewer] state:", pc.connectionState)
            if pc.connectionState in ("failed", "disconnected", "closed"):
                print("[Viewer] connection lost")
                lost_event.set()
                
        @pc.on("iceconnectionstatechange")
        async def on_ice_state():
            print("[Viewer] ice:", pc.iceConnectionState)
            if pc.iceConnectionState in ("failed", "disconnected", "closed"):
                print("[Viewer] ice connection lost")
                lost_event.set()
                
        # gửi ICE của viewer, đăng ký trước khi kết nối signaling
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
                    print("Failed to send ICE candidate: ", e)
                    
        # ====== mở cổng udp ======
        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            
        # Tạo kênh gửi dữ liệu
        command_channel = pc.createDataChannel("gcs_command")
        # heartbeat_channel = pc.createDataChannel("heartbeat_viewer")
        
        # COMMAND CHANNEL
        @command_channel.on("open")
        def on_open():
            print("command channel opened")
            asyncio.ensure_future(send_command_from_gcs(command_channel, lost_event, udp_sock, GCS_IP, TELEMETRY_PORT))
            
        @command_channel.on("close")
        def on_close():
            print("[Viewer] Heartbeat channel closed")
            # lost_event.set() 

        @pc.on("datachannel")
        def on_datachannel(channel):
            print("Data channel received:", channel.label)

            @channel.on("message")
            def on_message(message):
                # if channel.label == "heartbeat_publisher":
                #     if message == "ping":
                #         # print(f"Got ping from {channel.label} channel, sending pong")
                #         try:
                #             channel.send(f"pong")
                #         except Exception as e:
                #             print(f"{channel.label} channel send error: ", e)
                if channel.label == "telemetry":
                    # print(f"Got data from {channel.label} channel, forwarding udp")
                    try:
                        if not isinstance(message, bytes):
                            data = message.tobytes()
                            udp_sock.sendto(data, (GCS_IP, TELEMETRY_PORT))
                        else:
                            udp_sock.sendto(message, (GCS_IP, TELEMETRY_PORT))
                    except Exception as e:
                        print("UDP send error: ", e)
                
            @channel.on("close")
            def on_close():
                print(f"{channel.label} channel closed")
                # lost_event.set()

        # HEARTBEAT CHANNEL
        # @heartbeat_channel.on("open")
        # def on_open():
        #     print("Heartbeat channel opened")
        #     asyncio.ensure_future(heartbeat_task(heartbeat_channel, lost_event))
            
        # @heartbeat_channel.on("close")
        # def on_close():
        #     print("[Viewer] Heartbeat channel closed")
        #     lost_event.set() 

        # nhận được track video thì hiển thị hoặc là gửi cho thiết bị khác qua udp
        @pc.on("track")
        def on_track(track):
            print("[Viewer] Track received, track kind: ", track.kind)
            asyncio.ensure_future(opencv_display(track))
            # asyncio.ensure_future(opencv_to_gstreamer(track))
            # asyncio.ensure_future(send_video_packet_udp(track, udp_sock, GCS_IP, VIDEO_PORT))
            
            @track.on("ended")
            def _ended():
                print("[Viewer] track ended")
                lost_event.set()
                
        # chạy signaling loop song song
        # signaling_task = asyncio.create_task(signaling_loop(pc, on_icecandidate, role, timeout, SIGNALING_SERVER))
        signaling_task = asyncio.create_task(signaling_loop_pro(pc, lost_event, on_icecandidate, role, timeout, SIGNALING_SERVER))

        # chờ cho tới khi PC mất
        await lost_event.wait()
        signaling_task.cancel()
        udp_sock.close()
        print("Peer connection lost -> rebuild peer")
        await pc.close()        # đóng peer cũ
        print("pc closed")
        return "retry"

    except Exception as e:
        print("Error:", e)
        return "retry"
    
    finally:
        pass
    #     if pc:
    #         await pc.close()

# ====== main ======
async def main():
    parser = argparse.ArgumentParser(
        description="WebRTC video / data-channels implementation"
    )
    parser.add_argument(
        "--GCS_IP", default="127.0.0.1", help="GCS IP address to send data"
    )
    parser.add_argument(
        "--timeout", type=int, default=3, help="Signaling server connecting timeout (default: 3s)"
    )
    args = parser.parse_args()

    while True:
        result = await run(args.GCS_IP, args.timeout)
        if result == "retry":
            print("Got retry, retrying ...")
            await asyncio.sleep(PC_RETRY_TIME)  # retry sau vài giây
            continue
        else:
            break

if __name__ == "__main__":
    asyncio.run(main())
