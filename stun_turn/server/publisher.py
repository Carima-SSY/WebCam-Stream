# publisher.py
import asyncio
import json
import platform
import signal
import sys
from aiohttp import ClientSession
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaPlayer

STREAM_SERVER = "http://52.79.239.25:8080/publish"  # Signaling (STUN/TURN) Server Endpoint 
PUBLISHER_ID = "cam01"  # local pc id 

ICE_SERVERS = [
    RTCIceServer(urls=f"stun:52.79.239.25:3478"),
    RTCIceServer(urls=f"turn:52.79.239.25:3478?=udp", username="webrtcuser", credential="webrtcpass"),
    RTCIceServer(urls=f"turn:52.79.239.25:3478?transport=tcp", username="webrtcuser", credential="webrtcpass"),
    # TLS 사용 시(선택):
    # RTCIceServer(urls=f"turns:<TURN_HOST>:5349?transport=tcp", username="<TURN_USER>", credential="<TURN_PASS>"),
]
RTC_CONFIG = RTCConfiguration(iceServers=ICE_SERVERS)

def create_camera_player():
    sysname = platform.system().lower()
    if "darwin" in sysname or "mac" in sysname:
        print("ACCESS webcam in MAC OS")
        return MediaPlayer("default", format="avfoundation",
                           options={"framerate": "30", "video_size": "1280x720"})
    if "linux" in sysname:
        return MediaPlayer("/dev/video0", format="v4l2",
                           options={"framerate": "30", "video_size": "640x480"})
    if "windows" in sysname:
        return MediaPlayer("video=Integrated Camera", format="dshow",
                           options={"video_size": "640x480", "framerate": "30"})
    return None

async def main():
    pc = RTCPeerConnection(configuration=RTC_CONFIG)
    player = create_camera_player()
    print("CREATE CAMERA PLAYER END!!!")
    if player is None or player.video is None:
        print("Open Camera Failure")
        await pc.close()
        return

    pc.addTrack(player.video)

    @pc.on("connectionstatechange")
    async def on_state_change():
        print("publisher state:", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            await pc.close()

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    payload = {
        "publisher_id": PUBLISHER_ID,
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type,
    }

    async with ClientSession() as session:
        async with session.post(
            STREAM_SERVER, data=json.dumps(payload), headers={"Content-Type": "application/json"}
        ) as resp:
            if resp.status != 200:
                print("Server Response Error:", resp.status, await resp.text())
                await pc.close()
                return
            data = await resp.json()

    await pc.setRemoteDescription(RTCSessionDescription(sdp=data["sdp"], type=data["type"]))
    print(f"Publisher Connection Successfully (publisher_id={PUBLISHER_ID}). Enter Ctrl+C to End Connnection")

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    await stop.wait()
    await pc.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
