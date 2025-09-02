import gi, sys, argparse, time
gi.require_version("Gst", "1.0")
gi.require_version("GObject", "2.0")
from gi.repository import Gst, GObject

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5004)
    parser.add_argument("--rtcp-in", type=int, default=5005)
    parser.add_argument("--rtcp-out", type=int, default=5006)
    parser.add_argument("--sender-ip", default="192.168.3.10")
    parser.add_argument("--latency", type=int, default=120)
    args = parser.parse_args()

    Gst.init(None)
    loop = GObject.MainLoop()

    pipeline_str = f"""
    rtpbin name=rtpbin
      udpsrc port={args.port} caps="application/x-rtp, media=video, encoding-name=H264, payload=96, clock-rate=90000, ssrc=0x11111111"
        ! rtpjitterbuffer name=jb mode=1 latency={args.latency} drop-on-late=true do-retransmission=false
        ! rtph264depay ! h264parse ! avdec_h264 ! fakesink sync=false
      udpsrc port={args.rtcp-in} ! rtpbin.recv_rtcp_sink_0
      rtpbin.send_rtcp_src_0 ! udpsink host={args.sender_ip} port={args.rtcp-out} sync=false async=false
    """

    print("Pipeline:\n", pipeline_str)
    pipeline = Gst.parse_launch(pipeline_str)

    bus = pipeline.get_bus()
    bus.add_signal_watch()

    # Lấy reference tới jitterbuffer để đọc stats định kỳ
    jb = pipeline.get_by_name("jb")

    def on_message(bus, message, loop):
        t = message.type
        if t == Gst.MessageType.ERROR:
            err, dbg = message.parse_error()
            print("ERROR:", err, dbg)
            loop.quit()
        elif t == Gst.MessageType.ELEMENT:
            s = message.get_structure()
            if s and s.get_name() == "GstRTPJitterBuffer":
                # Thường gửi các notify về lost/late…
                pass
        elif t == Gst.MessageType.EOS:
            loop.quit()
        return True

    bus.connect("message", on_message, loop)

    # Timer in stats
    def print_stats():
        try:
            # Một số prop phổ biến (tuỳ phiên bản GStreamer):
            # "num-packets-expected", "num-packets-rtx-recovered", "num-packets-lost", "num-packets-dropped", "jitter"
            # "rtx-count" tùy phiên bản
            lost = jb.get_property("num-packets-lost")
            expected = jb.get_property("num-packets-expected")
            dropped = jb.get_property("num-packets-dropped")
            jitter = jb.get_property("jitter")
            recovered = 0
            try:
                recovered = jb.get_property("num-packets-rtx-recovered")
            except Exception:
                pass
            loss_rate = 0.0
            if expected and expected > 0:
                loss_rate = 100.0 * float(lost) / float(expected)
            print(f"[STATS] expected={expected} lost={lost} recovered={recovered} dropped={dropped} jitter={jitter:.3f}ms loss={loss_rate:.2f}%")
        except Exception as e:
            print("stats err:", e)
        return True  # keep

    # In mỗi 1s
    GObject.timeout_add_seconds(1, print_stats)

    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        print("Failed to PLAY")
        sys.exit(1)

    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.set_state(Gst.State.NULL)

if __name__ == "__main__":
    main()
