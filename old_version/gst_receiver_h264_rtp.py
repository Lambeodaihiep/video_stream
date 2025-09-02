import sys, argparse
import gi
gi.require_version("Gst", "1.0")
gi.require_version("GObject", "2.0")
from gi.repository import Gst, GObject

def build_pipeline(args):
    # rtpjitterbuffer: mode=1 (sử dụng timestamp), latency ms
    # drop-on-late=true để bỏ gói tới muộn (tránh tích lũy trễ)
    # sync=false ở sink để hiển thị nhanh (giảm delay)
    pipeline_str = f"""
    udpsrc port={args.port} caps="application/x-rtp, media=video, encoding-name=H264, payload=96" !
      rtpjitterbuffer mode=1 latency={args.latency} !
      rtph264depay !
      h264parse !
      {args.decoder} !
      videoconvert !
      fpsdisplaysink text-overlay=true
    """
    return pipeline_str

def on_bus_message(bus, message, loop):
    t = message.type
    if t == Gst.MessageType.ERROR:
        err, dbg = message.parse_error()
        print("ERROR:", err, dbg)
        loop.quit()
    elif t == Gst.MessageType.EOS:
        print("EOS")
        loop.quit()
    return True

def main():
    parser = argparse.ArgumentParser(description="GStreamer H.264 RTP Receiver")
    parser.add_argument("--port", type=int, default=5055)
    parser.add_argument("--latency", type=int, default=120, help="ms, jitter buffer")
    parser.add_argument("--decoder", default="avdec_h264", help="avdec_h264/vaapih264dec/nvh264dec")
    args = parser.parse_args()

    Gst.init(None)
    loop = GObject.MainLoop()

    pipeline_str = build_pipeline(args)
    print("Pipeline:\n", pipeline_str)

    pipeline = Gst.parse_launch(pipeline_str)
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", on_bus_message, loop)

    ret = pipeline.set_state(Gst.State.PLAYING)
    if ret == Gst.StateChangeReturn.FAILURE:
        print("Failed to set pipeline to PLAYING")
        sys.exit(1)

    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.set_state(Gst.State.NULL)

if __name__ == "__main__":
    main()
