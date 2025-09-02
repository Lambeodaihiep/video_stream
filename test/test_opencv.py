import cv2

# Create a VideoCapture object and specify the video file path
# If the video is in the current working directory, just the filename is sufficient
cap = cv2.VideoCapture('test.mp4')

# Check if the video file was opened successfully
if not cap.isOpened():
    print("Error: Could not open video file.")
else:
    # Loop through frames until the end of the video
    while True:
        # Read a frame from the video
        ret, frame = cap.read()

        # If ret is False, it means the end of the video has been reached
        if not ret:
            break

        # Display the frame (optional)
        print(type(frame.tobytes()))
        cv2.imshow('Video Frame', frame)

        # Press 'q' to exit the video display
        if cv2.waitKey(25) & 0xFF == ord('q'):
            break

    # Release the VideoCapture object and destroy all OpenCV windows
    cap.release()
    cv2.destroyAllWindows()
