import cv2
import boto3
from kinesis_video_producer import KinesisVideoProducer
import time
import sys
import threading

# AWS KVS 설정
STREAM_NAME = "carima-hub-test-stream"
AWS_REGION = "ap-northeast-2"

# 웹캠 및 스트리밍 설정
CAP_WIDTH = 1280
CAP_HEIGHT = 720
CAP_FPS = 30
CAP_VIDEO_INDEX = 0

# Kinesis Video Producer 객체 생성
try:
    producer = KinesisVideoProducer(
        stream_name=STREAM_NAME,
        region=AWS_REGION,
        # KVS_PRODUCER_LIBRARY_PATH 환경 변수를 통해 SDK 라이브러리 경로를 지정할 수 있습니다.
        log_level="info"
    )
except Exception as e:
    print(f"Error creating KinesisVideoProducer: {e}")
    sys.exit(1)

# OpenCV를 사용하여 웹캠 객체 생성
cap = cv2.VideoCapture(CAP_VIDEO_INDEX, cv2.CAP_DSHOW) # 윈도우에서는 CAP_DSHOW, 맥에서는 제거
if not cap.isOpened():
    print("웹캠을 찾을 수 없습니다. 인덱스를 확인하세요.")
    sys.exit(1)

# 웹캠 해상도 및 FPS 설정
cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAP_WIDTH)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAP_HEIGHT)
cap.set(cv2.CAP_PROP_FPS, CAP_FPS)

print("KVS 스트리밍 시작. 중지하려면 Ctrl+C를 누르세요.")

try:
    with producer.stream_session() as session:
        print("KVS 스트림 세션 시작")
        frame_timestamp = time.time()
        
        while True:
            ret, frame = cap.read()
            if not ret:
                print("프레임 읽기 실패")
                break
            
            # KVS 세션에 프레임 추가
            session.put_frame(
                frame.tobytes(), # OpenCV 프레임 데이터를 바이트로 변환
                is_key_frame=True, # 키 프레임 여부
                frame_timestamp=frame_timestamp
            )
            
            frame_timestamp += 1.0 / CAP_FPS

except KeyboardInterrupt:
    print("\n스트리밍을 중단합니다.")
except Exception as e:
    print(f"An error occurred: {e}")
finally:
    if 'cap' in locals() and cap.isOpened():
        cap.release()
    print("모든 리소스가 정리되었습니다.")