import cv2

cap = cv2.VideoCapture("http://192.168.0.100:8080/video")

while True:
    ret, frame = cap.read()
    if not ret:
        print("Not found camera")
        break
    
    frame = cv2.resize(frame, (1080, 720))
    cv2.imshow("Viewer", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

cap.release()
cv2.destroyAllWindows()
    
