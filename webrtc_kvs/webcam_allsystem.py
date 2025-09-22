import cv2
import subprocess
import shlex
import sys
import os

# AWS KVS 설정
STREAM_NAME = "YOUR_KVS_STREAM_NAME"  # KVS 스트림 이름으로 변경
AWS_REGION = "ap-northeast-2"  # AWS 리전으로 변경

# 웹캠 및 스트리밍 설정
CAP_WIDTH = 1280
CAP_HEIGHT = 720
CAP_FPS = 30
CAP_VIDEO_INDEX = 0  # 단일 웹캠이므로 0번 인덱스 사용

def start_kvs_streaming():
    """
    운영체제에 따라 다른 FFmpeg 명령어를 실행하여 KVS로 스트리밍합니다.
    """
    if sys.platform.startswith('win'):
        # Windows 환경: FFmpeg가 웹캠을 DirectShow로 접근
        # Python 스크립트가 OpenCV로 프레임을 읽어서 FFmpeg에 파이프
        cap = cv2.VideoCapture(CAP_VIDEO_INDEX, cv2.CAP_DSHOW)
        if not cap.isOpened():
            print("웹캠을 찾을 수 없습니다. 인덱스를 확인하세요.")
            return

        command = (
            f"ffmpeg -f rawvideo -vcodec rawvideo -pix_fmt bgr24 "
            f"-s {CAP_WIDTH}x{CAP_HEIGHT} -r {CAP_FPS} -i - "
            f"-c:v h264 -f matroska - | gst-launch-1.0 fdsrc fd=0 ! matroskademux ! video/x-h264, stream-format=byte-stream, alignment=au ! kvssink stream-name={STREAM_NAME} aws-region={AWS_REGION}"
        )
        print("Executing command on Windows...\n", command)
        
        process = subprocess.Popen(command, shell=True, stdin=subprocess.PIPE, stdout=sys.stdout, stderr=sys.stderr)
        
        try:
            while True:
                ret, frame = cap.read()
                if not ret: break
                process.stdin.write(frame.tobytes())
        except KeyboardInterrupt:
            print("\n스트리밍 중단")
        finally:
            cap.release()
            process.stdin.close()
            process.wait()

    elif sys.platform == 'darwin':
        # macOS 환경: FFmpeg가 웹캠을 avfoundation으로 직접 접근
        command = (
            f"ffmpeg -f avfoundation -r {CAP_FPS} -s {CAP_WIDTH}x{CAP_HEIGHT} -i \"{CAP_VIDEO_INDEX}:{CAP_VIDEO_INDEX}\" "
            f"-c:v h264 -f matroska - | gst-launch-1.0 fdsrc fd=0 ! matroskademux ! video/x-h264, stream-format=byte-stream, alignment=au ! kvssink stream-name={STREAM_NAME} aws-region={AWS_REGION}"
        )
        print("Executing command on macOS...\n", command)
        
        try:
            subprocess.run(command, shell=True, check=True)
        except KeyboardInterrupt:
            print("\n스트리밍 중단")
            
    elif sys.platform.startswith('linux'):
        # 라즈베리파이 / Linux 환경: FFmpeg가 v4l2로 직접 접근
        # v4l2 장치는 일반적으로 /dev/video0
        command = (
            f"ffmpeg -f v4l2 -r {CAP_FPS} -s {CAP_WIDTH}x{CAP_HEIGHT} -i /dev/video{CAP_VIDEO_INDEX} "
            f"-c:v h264 -f matroska - | gst-launch-1.0 fdsrc fd=0 ! matroskademux ! video/x-h264, stream-format=byte-stream, alignment=au ! kvssink stream-name={STREAM_NAME} aws-region={AWS_REGION}"
        )
        print("Executing command on Linux...\n", command)
        
        try:
            subprocess.run(command, shell=True, check=True)
        except KeyboardInterrupt:
            print("\n스트리밍 중단")

    else:
        print("지원되지 않는 운영체제입니다.")

if __name__ == "__main__":
    start_kvs_streaming()