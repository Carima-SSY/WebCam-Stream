import asyncio
import json
import platform
import signal
import sys
import numpy as np
import time

# aiortc 및 picamera2 관련 모듈 임포트
from aiohttp import ClientSession
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer, MediaStreamTrack
from av import VideoFrame # av 라이브러리가 VideoFrame 처리를 담당
try:
    from picamera2 import Picamera2
    PI_CAMERA_AVAILABLE = True
except ImportError:
    PI_CAMERA_AVAILABLE = False
    print("Warning: picamera2 not installed. CSI direct connect unavailable.")


# --- 전역 설정 상수 (클래스 외부에 유지) ---
STREAM_SERVER = "http://52.79.239.25:8080/publish"  # Signaling (STUN/TURN) Server Endpoint 
PUBLISHER_ID = "cam01"  # local pc id 
TARGET_WIDTH, TARGET_HEIGHT = 640, 360 # 목표 해상도

ICE_SERVERS = [
    RTCIceServer(urls=f"stun:52.79.239.25:3478"),
    RTCIceServer(urls=f"turn:52.79.239.25:3478?=udp", username="webrtcuser", credential="webrtcpass"),
    RTCIceServer(urls=f"turn:52.79.239.25:3478?transport=tcp", username="webrtcuser", credential="webrtcpass"),
]
RTC_CONFIG = RTCConfiguration(iceServers=ICE_SERVERS)

# --- 1. CSI 직결을 위한 커스텀 미디어 트랙 (Picamera2 사용) ---

class Picam2Track(MediaStreamTrack):
    """
    라즈베리 파이 CSI 카메라 모듈 3를 사용하는 커스텀 MediaStreamTrack.
    picamera2를 통해 하드웨어 가속을 활용합니다.
    """
    kind = "video"

    def __init__(self, width=TARGET_WIDTH, height=TARGET_HEIGHT):
        super().__init__()
        print(f"Initializing Picamera2 for CSI stream: {width}x{height}")
        self.picam2 = Picamera2()
        
        # BGR888 포맷으로 설정하여 OpenCV의 BGR 포맷과 호환되게 함
        config = self.picam2.create_video_configuration(
            main={"size": (width, height), "format": "BGR888"},
            queue=False 
        )
        self.picam2.configure(config)
        self.picam2.start()

    async def recv(self):
        # aiortc가 요구하는 VideoFrame 객체를 반환
        
        # 1. 프레임 캡처 (NumPy 배열)
        frame_data = self.picam2.capture_array()
        
        # 2. av.VideoFrame 객체로 변환
        # Picamera2는 BGR 순서로 데이터를 반환하므로 'bgr24' 포맷 지정
        frame = VideoFrame.from_ndarray(frame_data, format="bgr24")
        
        return frame

    def stop(self):
        # 자원 해제
        self.picam2.stop()
        print("Picamera2 stopped.")
        super().stop()


# --- 2. OS에 따라 카메라 객체를 생성하는 통합 함수 ---

def create_media_source():
    """OS에 따라 가장 적합한 카메라 미디어 트랙 소스를 생성하여 반환합니다."""
    sysname = platform.system().lower()
    
    # 1. 라즈베리 파이 CSI 직결 환경 (가장 높은 우선순위)
    # linux이면서 picamera2가 설치되어 있다면 CSI 직결로 판단
    if "linux" in sysname and PI_CAMERA_AVAILABLE:
        # Picam2Track 커스텀 객체 반환 (CSI 직결)
        return Picam2Track(TARGET_WIDTH, TARGET_HEIGHT)
    
    # 2. Linux 환경 (USB 웹캠 등)
    if "linux" in sysname:
        # USB 웹캠용 MediaPlayer 반환
        return MediaPlayer("/dev/video0", format="v4l2",
                           options={"framerate": "30", "video_size": f"{TARGET_WIDTH}x{TARGET_HEIGHT}"})

    # 3. macOS 환경
    if "darwin" in sysname or "mac" in sysname:
        return MediaPlayer("default", format="avfoundation",
                           options={"framerate": "30", "video_size": "1280x720"})

    # 4. Windows 환경
    if "windows" in sysname:
        return MediaPlayer("video=Integrated Camera", format="dshow",
                           options={"video_size": f"{TARGET_WIDTH}x{TARGET_HEIGHT}", "framerate": "30"})
        
    return None

# --- 3. WebRTC Publisher 클래스 (수정 필요 없음) ---

class WebRTCPublisher:
    def __init__(self, publisher_id: str, config: RTCConfiguration):
        self.publisher_id = publisher_id
        self.config = config
        self.pc = None
        # self.player 대신 self.media_source로 이름 변경
        self.media_source = None 
        self.stop_event = asyncio.Event()

    async def _handle_signal_response(self, sdp_offer: str, sdp_type: str):
        payload = {
            "publisher_id": self.publisher_id,
            "sdp": sdp_offer,
            "type": sdp_type,
        }

        async with ClientSession() as session:
            async with session.post(
                STREAM_SERVER, data=json.dumps(payload), headers={"Content-Type": "application/json"}
            ) as resp:
                if resp.status != 200:
                    print("Server Response Error:", resp.status, await resp.text())
                    return None
                
                return await resp.json()
            
    async def _on_state_change(self):
        print("Publisher Connection State:", self.pc.connectionState)
        if self.pc.connectionState in ("failed", "closed", "disconnected"):
            print(f"Connection closed due to state: {self.pc.connectionState}")
            await self.stop()

    async def run(self):
        self.pc = RTCPeerConnection(configuration=self.config)
        
        # 수정된 함수 호출
        self.media_source = create_media_source()
        
        print("CREATE MEDIA SOURCE END!!!")
        
        # MediaStreamTrack 객체 확인 (Picam2Track 또는 MediaPlayer)
        video_track = self.media_source.video if isinstance(self.media_source, MediaPlayer) else self.media_source

        if video_track is None:
            print("Open Camera Failure. Closing connection.")
            await self.pc.close()
            return

        self.pc.addTrack(video_track)
        
        # register event handler
        self.pc.on("connectionstatechange")(self._on_state_change)

        # Create Offer & Set Local Description
        offer = await self.pc.createOffer()
        await self.pc.setLocalDescription(offer)
        
        # Send to Signaling Server
        answer_data = await self._handle_signal_response(
            self.pc.localDescription.sdp, 
            self.pc.localDescription.type
        )
        
        if not answer_data:
            await self.stop()
            return
            
        # 3. Remote SDP (Answer) 설정
        await self.pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer_data["sdp"], type=answer_data["type"])
        )
        
        print(f"Publisher Connection Successfully (ID={self.publisher_id}).")
        print("Enter Ctrl+C to End Connnection")

        # Infinite Await
        self._setup_signal_handlers()
        await self.stop_event.wait()
        
    def _setup_signal_handlers(self):
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop_event.set)
            except NotImplementedError:
                pass 

    async def stop(self):
        if self.pc and self.pc.connectionState not in ("closed", "failed"):
            await self.pc.close()
        
        # Picam2Track의 stop 호출 (MediaPlayer는 stop()이 필요 없음)
        if hasattr(self.media_source, 'stop'):
             self.media_source.stop()

        # Streaming Stop Event Occur
        self.stop_event.set()


if __name__ == "__main__":
    publisher = WebRTCPublisher(publisher_id=PUBLISHER_ID, config=RTC_CONFIG)
    try:
        asyncio.run(publisher.run())
    except KeyboardInterrupt:
        print("\nPublisher process interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)