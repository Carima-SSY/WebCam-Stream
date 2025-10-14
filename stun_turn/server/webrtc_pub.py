import asyncio
import json
import platform
import signal
import sys
from aiohttp import ClientSession
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer

# --- 전역 설정 상수 (클래스 외부에 유지) ---
STREAM_SERVER = "http://52.79.239.25:8080/publish"  # Signaling (STUN/TURN) Server Endpoint 
PUBLISHER_ID = "cam01"  # local pc id 

ICE_SERVERS = [
    RTCIceServer(urls=f"stun:52.79.239.25:3478"),
    RTCIceServer(urls=f"turn:52.79.239.25:3478?=udp", username="webrtcuser", credential="webrtcpass"),
    RTCIceServer(urls=f"turn:52.79.239.25:3478?transport=tcp", username="webrtcuser", credential="webrtcpass"),
]
RTC_CONFIG = RTCConfiguration(iceServers=ICE_SERVERS)

# -----------------------------------------------

def create_camera_player():
    sysname = platform.system().lower()
    if "darwin" in sysname or "mac" in sysname:
        return MediaPlayer("default", format="avfoundation",
                           options={"framerate": "30", "video_size": "1280x720"})
    if "linux" in sysname:
        return MediaPlayer("/dev/video0", format="v4l2",
                           options={"framerate": "30", "video_size": "640x480"})
    if "windows" in sysname:
        return MediaPlayer("video=Integrated Camera", format="dshow",
                           options={"video_size": "640x480", "framerate": "30"})
    return None

class WebRTCPublisher:
    def __init__(self, publisher_id: str, config: RTCConfiguration):
        self.publisher_id = publisher_id
        self.config = config
        self.pc = None
        self.player = None
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
        self.player = create_camera_player()
        
        print("CREATE CAMERA PLAYER END!!!")
        if self.player is None or self.player.video is None:
            print("Open Camera Failure. Closing connection.")
            await self.pc.close()
            return

        self.pc.addTrack(self.player.video)
        
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
                pass # Windows에서는 signal handler가 지원되지 않을 수 있음

    async def stop(self):
        if self.pc and self.pc.connectionState not in ("closed", "failed"):
            await self.pc.close()
        
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