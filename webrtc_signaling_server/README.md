# cấu hình signaling server và turn server trong file config.py

# chạy file override_aiortc.py để tự động ghi đè file rtcrtpreceiver.py

# chạy viewer.py để nhận dữ liệu từ webrtc

# xem video bằng gstreamer thì chạy lệnh
gst-launch-1.0 -v udpsrc port=5001 ! h264parse ! avdec_h264 ! videoconvert ! video/x-raw,format=BGRA ! autovideosink sync=false