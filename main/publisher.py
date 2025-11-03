import asyncio
import json
import platform
import signal
import sys, os
import numpy as np
import time

# import module related to aiortc and picamera2
from aiohttp import ClientSession
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer, MediaStreamTrack
from av import VideoFrame # process VideoFrame
try:
    from picamera2 import Picamera2
    PI_CAMERA_AVAILABLE = True
except ImportError:
    PI_CAMERA_AVAILABLE = False
    print("Warning: picamera2 not installed. CSI direct connect unavailable.")
    
# --- global constant ---
def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)

def set_publisher_config():
    with open(get_resource_path('pub-config.json'), 'r', encoding='utf-8') as file:
        pub_cfg = json.load(file) 
        
    return pub_cfg['host']['ip'], pub_cfg['host']['user'], pub_cfg['host']['pass'], pub_cfg['publisher']['id'], pub_cfg['video']['type'], pub_cfg['video']['resol']['width'], pub_cfg['video']['resol']['height']
HOST_IP, HOST_USER, HOST_PASS, PUB_ID, VIDEO, WIDTH, HEIGHT = set_publisher_config()

STREAM_SERVER = f"http://{HOST_IP}:8080/publish"  

ICE_SERVERS = [
    RTCIceServer(urls=f"stun:{HOST_IP}:3478"),
    RTCIceServer(urls=f"turn:{HOST_IP}:3478?transport=udp", username=f"{HOST_USER}", credential=f"{HOST_PASS}"), 
    RTCIceServer(urls=f"turn:{HOST_IP}:3478?transport=tcp", username=f"{HOST_USER}", credential=f"{HOST_PASS}"),
    # ...
    # TLS 사용 시(선택):
    # RTCIceServer(urls=f"turns:{HOST_IP}:5349?transport=tcp", username=f"{HOST_USER}", credential=f"{HOST_PASS}"),
]
RTC_CONFIG = RTCConfiguration(iceServers=ICE_SERVERS)

# --- custom media track for CSI connnection (using Picamera2) ---

class Picam2Track(MediaStreamTrack):
    """
    MediaStreamTrack (using Raspberry PI CSI Camera Module 3)
    use hardware acceleration with pi camera2
    """
    kind = "video"

    def __init__(self, width=WIDTH, height=HEIGHT):
        super().__init__()
        print(f"Initializing Picamera2 for CSI stream: {width}x{height}")
        self.picam2 = Picamera2()
        
        # set BGR888 format to be compatible with BGR format of OpenCV
        config = self.picam2.create_video_configuration(
            main={"size": (width, height), "format": "BGR888"},
            queue=False 
        )
        self.picam2.configure(config)
        self.picam2.start()

    async def recv(self):
        # return VideoFrame class object that aiortc require
                
        # First, capture frame (NumPy array)
        frame_data = self.picam2.capture_array()
        
        # Second, return av.VideoFrame class object
        # select format to bgr24 because Picamera2 return data to BGR sequence 
        frame = VideoFrame.from_ndarray(frame_data, format="bgr24")
        
        return frame

    def stop(self):
        # resource deactivated
        self.picam2.stop()
        print("Picamera2 stopped.")
        super().stop()


# --- create media source (window, mac, linux) ---

def create_media_source():
    sysname = platform.system().lower()
    
    # [1] CSI Interface in Raspberry PI
    if "linux" in sysname and PI_CAMERA_AVAILABLE:
        return Picam2Track(WIDTH, HEIGHT)
    
    # [2] USB Camera in Linux
    if "linux" in sysname:
        # USB 웹캠용 MediaPlayer 반환
        return MediaPlayer("/dev/video0", format="v4l2",
                           options={"framerate": "30", "video_size": f"{WIDTH}x{HEIGHT}"})

    # [3] USB Camera in macOS
    if "darwin" in sysname or "mac" in sysname:
        return MediaPlayer("default", format="avfoundation",
                           options={"framerate": "30", "video_size": f"{WIDTH}x{HEIGHT}"})

    # [4] USB Camera in Window
    if "windows" in sysname:
        return MediaPlayer(f"video={VIDEO}", format="dshow",
                           options={"video_size": f"{WIDTH}x{HEIGHT}", "framerate": "30"})
        
    return None

# --- 3. WebRTC Publisher 클래스 ---

class WebRTCPublisher:
    def __init__(self, publisher_id: str, config: RTCConfiguration):
        self.publisher_id = publisher_id
        self.config = config
        self.pc = None
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
        
        # create media source
        self.media_source = create_media_source()
        
        print("CREATE MEDIA SOURCE END!!!")
        
        # check MediaStreamTrack Object (Picam2Track or MediaPlayer)
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
            
        # set Remote SDP (Answer)
        await self.pc.setRemoteDescription(
            RTCSessionDescription(sdp=answer_data["sdp"], type=answer_data["type"])
        )
        
        print(f"Publisher Connection Successfully (ID={self.publisher_id}).")
        print("Enter Ctrl+C to End Connnection")

        # Infinite Await
        self._setup_signal_handlers()
        await self.stop_event.wait()
        
        # when stop event occur, pc.close() call one time more
        await self.pc.close() 
        
        
    def _setup_signal_handlers(self):
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.stop_event.set)
            except NotImplementedError:
                pass 

    async def stop(self):
        # call stop method in Picam2Track (MediaPlayer do not need to call stop())
        if hasattr(self.media_source, 'stop'):
             self.media_source.stop()

        # Streaming Stop Event Occur
        self.stop_event.set()
        
        # stop and close RTCPeerConnection
        if self.pc and self.pc.connectionState not in ("closed", "failed"):
             await self.pc.close() 


if __name__ == "__main__":
    publisher = WebRTCPublisher(publisher_id=PUB_ID, config=RTC_CONFIG)
    try:
        asyncio.run(publisher.run())
    except KeyboardInterrupt:
        print("\nPublisher process interrupted by user.")
        sys.exit(0)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)