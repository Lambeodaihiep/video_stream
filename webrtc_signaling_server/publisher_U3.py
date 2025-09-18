import argparse, serial, asyncio, websockets, json, cv2, time
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc import RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer
from aiortc.sdp import candidate_from_sdp, candidate_to_sdp
from aiortc.codecs import get_capabilities
from utils import (signaling_loop,
                   uart_reader,
                   signaling_loop_pro,
                   BlackFrameTrack,
                   rtsp_track,
                   udp_unicast_track,
                   udp_multicast_track,
                   send_telemetry_from_udp)
from config import *

# cái này để ép server dùng codec h264
video_caps = get_capabilities("video")
h264_codecs = [c for c in video_caps.codecs if c.mimeType.lower() == "video/h264"]
role = "publisher"

# ====== Camera IP ======
#rtsp_url = "rtsp://192.168.0.101:8080/h264.sdp"
#rtsp_url = "rtsp://192.168.0.107:8554/test"
rtsp_url = "rtsp://admin:123456a%40@192.168.5.69:554/Streaming/Channels/101"
# rtsp_url = "rtsp://admin:123456!Vht@192.168.1.120:18554/h264"

async def run(camera: str, AP: str, COM_port: str, baudrate: int, timeout: int):
    pc = None
    ser = None
    camera_source_1 = None
    camera_source_2 = None
    try:
        # Tạo peer connection
        pc = RTCPeerConnection(RTCConfiguration(iceServers=ice_servers))
        lost_event = asyncio.Event()
        
        # Dùng track giả để không block signaling
        black_track_1 = BlackFrameTrack()
        black_track_2 = BlackFrameTrack()
        sender_1 = pc.addTrack(black_track_1)
        sender_2 = pc.addTrack(black_track_2)
        
        if camera == "on":
            print(f"[{time.time()}] reading camera")
            # kiểm tra RTSP camera bằng opencv trước
            # cap = cv2.VideoCapture(rtsp_url)
            # if not cap.isOpened():
               # print("RTSP camera not available. Try again ...")
               # return "no_camera"
            # cap.release()

            camera_source_1 = rtsp_track(rtsp_url)
            print(f"[{time.time()}] camera 1 ok")
            
            camera_source_2 = udp_unicast_track(udp_unicast_port)
            print(f"[{time.time()}] camera 2 ok")
            
        
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
        if camera_source_1 is not None and camera_source_1.video:
            print("hehehehe 1")
            camera_source_1.video._id = "cam1"
            #pc.addTrack(camera_source.video)
            sender_1.replaceTrack(camera_source_1.video)
            
        if camera_source_2 is not None and camera_source_2.video:
            print("hehehehe 2")
            camera_source_2.video._id = "cam2"
            #pc.addTrack(video_source.video)
            sender_2.replaceTrack(camera_source_2.video)
        # Ép server codec h264
        for transceiver in pc.getTransceivers():
            transceiver.setCodecPreferences(h264_codecs)
        
        # mở kênh serial
        if AP == "uart":
            ser = serial.Serial(COM_port, baudrate=baudrate, timeout=1)
            ser.reset_input_buffer() 
            ser.reset_output_buffer() 
            print(f"[Publisher] UART opened at {COM_port} {baudrate}")

        # Tạo kênh gửi dữ liệu
        telemetry_channel = pc.createDataChannel("telemetry")
        #heartbeat_channel = pc.createDataChannel("heartbeat_publisher")
        
        # TELEMETRY CHANNEL
        @telemetry_channel.on("open")
        def on_open():
            print(f"[{time.time()}] Telemetry channel opened")
            #asyncio.ensure_future(send_periodic(telemetry_channel))
            if ser is not None:
                asyncio.ensure_future(uart_reader(telemetry_channel, ser))
            if AP == "udp":
                asyncio.ensure_future(send_telemetry_from_udp(telemetry_channel, lost_event, udp_multicast_group, udp_multicast_telemetry_port))

        @telemetry_channel.on("message")
        def on_message(message):
            print(f"Got from subscriber: {message}")
            
        @telemetry_channel.on("close")
        def on_close():
            print("[Publisher] telemetry channel closed")
            lost_event.set()
            
        @pc.on("datachannel")
        def on_datachannel(channel):
            print(f"[{time.time()}] Data channel received:", channel.label)

            @channel.on("message")
            def on_message(message):
                # if channel.label == "heartbeat_viewer":
                    # if message == "ping":
                        # try:
                            # channel.send("pong")
                        # except Exception as e:
                            # print(f"{channel.label} channel send error: {e}")

                if channel.label == "gcs_command":
                    print(f"{channel.label} channel got: {message}, sending to uart")
                    if ser is not None:
                        ser.write(message)
                
            @channel.on("close")
            def on_close():
                print(f"{channel.label} channel closed")
                lost_event.set()
        # HEARTBEAT CHANNEL
        # @heartbeat_channel.on("open")
        # def on_open():
            # print("heartbeat channel opened")
            # #asyncio.ensure_future(heartbeat_task(heartbeat_channel, lost_event))

        # @heartbeat_channel.on("close")
        # def on_close():
            # print("[Publisher] heartbeat channel closed")
            # lost_event.set()
            
        # chạy signaling loop song song
        #signaling_task = asyncio.create_task(signaling_loop(pc, on_icecandidate, role, timeout, SIGNALING_SERVER))
        signaling_task = asyncio.create_task(signaling_loop_pro(pc, lost_event, on_icecandidate, role, timeout, SIGNALING_SERVER))

        # chờ cho tới khi PC mất
        await lost_event.wait()
        signaling_task.cancel()
        print("Peer connection lost -> rebuild peer")
        #for transceiver in pc.getTransceivers():
        #    if transceiver.receiver.track:
        #        await transceiver.receiver.track.stop()
        #for sender in pc.getSenders():
        #    await sender.stop()
        await pc.close()        # đóng peer cũ
        print("pc closed")
        return "retry"
    
    except Exception as e:
        print("Error:", e)
        return "retry"
    
    finally:
        print("Cleaning ...", end=" ")
        if pc is not None:
            await pc.close()
        if camera_source_1 is not None:
            camera_source_1.video.stop()
        if camera_source_2 is not None:
            camera_source_2.video.stop()
        print("Done")


# ====== main ======
async def main():
    parser = argparse.ArgumentParser(
        description="WebRTC video / data-channels implementation"
    )
    parser.add_argument(
        "--camera", type=str, default="off"
    )
    parser.add_argument(
        "--AP", type=str, default="off", help="pass 'uart' or 'udp' or 'off'"
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
        result = await run(args.camera, args.AP, args.COM_port, args.baudrate, args.timeout)
        if result in ["retry", "no_camera"]:
            if result == "retry":
                print("Got retry, retrying ...")
            await asyncio.sleep(PC_RETRY_TIME)  # retry sau vài giây
            continue
        else:
            break


if __name__ == "__main__":
    print(f"[{time.time()}] starting")
    asyncio.run(main())
