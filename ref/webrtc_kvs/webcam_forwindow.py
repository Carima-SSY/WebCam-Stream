import cv2
import subprocess
import shlex
import sys
import time

# AWS KVS 설정 정보
STREAM_NAME = "carima-hub-test-stream"
AWS_REGION = "ap-northeast-2"

# 웹캠 설정
CAP_WIDTH = 1280
CAP_HEIGHT = 720
CAP_FPS = 30
CAP_VIDEO_INDEX = 0  # 단일 웹캠이므로 0번 인덱스 사용

def start_kvs_streaming():
    # GStreamer 및 FFmpeg 실행에 필요한 환경 변수 설정
    # GStreamer와 KVS 플러그인이 PATH에 없으면 이 부분을 설정해줘야 합니다.
    # 예: os.environ['GST_PLUGIN_PATH'] = '/path/to/gst_kvssink_plugin'

    # FFmpeg 명령어 구성: 웹캠 입력 -> GStreamer 출력
    # -i - : 표준 입력(stdin)에서 데이터 읽기
    # -f rawvideo -vcodec rawvideo ... : OpenCV에서 가져온 RAW 비디오 데이터
    # -c:v h264 : 출력 비디오 코덱을 H.264로 지정
    command = (
        f"ffmpeg -f dshow -i video={CAP_VIDEO_INDEX} "
        f"-c:v h264 -f matroska - | gst-launch-1.0 fdsrc fd=0 ! matroskademux ! video/x-h264, stream-format=byte-stream, alignment=au ! kvssink stream-name={STREAM_NAME} aws-region={AWS_REGION}"
    )

    print("Executing command:\n", command)
    
    try:
        # Execute Subprocess (Async)
        process = subprocess.Popen(command, shell=True, stdin=subprocess.PIPE, stdout=sys.stdout, stderr=sys.stderr)
        
        # Open Webcam with OpenCV
        cap = cv2.VideoCapture(CAP_VIDEO_INDEX)
        if not cap.isOpened():
            print("Webcam is not Found. Please Check Index...")
            sys.exit(1)
            
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAP_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAP_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS, CAP_FPS)

        print("Start KVS Streaming. If you want to pause, enter Ctrl+C key.")
        
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Failure of Reading Frame..")
                break
            
            # 프레임 데이터를 FFmpeg의 표준 입력으로 전달
            process.stdin.write(frame.tobytes())

    except KeyboardInterrupt:
        print("\nStop KVS Streaming.")
    finally:
        if 'cap' in locals() and cap.isOpened():
            cap.release()
        if 'process' in locals() and process.poll() is None:
            process.stdin.close()
            process.terminate()
            process.wait()
        print("All Resource is cleared.")

if __name__ == "__main__":
    start_kvs_streaming()