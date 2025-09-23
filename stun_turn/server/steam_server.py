# stream_server.py
import asyncio
from aiohttp import web
import aiohttp_cors
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaRelay

# ===== ICE servers (coturn) =====
ICE_SERVERS = [
    RTCIceServer(urls=f"stun:<TURN_HOST>:3478"),
    RTCIceServer(urls=f"turn:<TURN_HOST>:3478?transport=udp", username="<TURN_USER>", credential="<TURN_PASS>"),
    RTCIceServer(urls=f"turn:<TURN_HOST>:3478?transport=tcp", username="<TURN_USER>", credential="<TURN_PASS>"),
    # TLS(선택):
    # RTCIceServer(urls=f"turns:<TURN_HOST>:5349?transport=tcp", username="<TURN_USER>", credential="<TURN_PASS>"),
]
RTC_CONFIG = RTCConfiguration(iceServers=ICE_SERVERS)
# =================================

relay = MediaRelay()

# 다수 퍼블리셔/뷰어 관리
publishers = {}  # { publisher_id: { "pc": RTCPeerConnection, "track": VideoStreamTrack } }
viewer_pcs = set()

async def publish(request: web.Request):
    """
    퍼블리셔가 publisher_id와 SDP offer를 POST로 보냄
    -> 서버는 recvonly로 받아 트랙 저장 -> answer 반환
    바디: { "publisher_id": "cam01", "sdp": "...", "type": "offer" }
    """
    params = await request.json()
    publisher_id = params.get("publisher_id")
    if not publisher_id:
        return web.Response(status=400, text="publisher_id is required")

    pc = RTCPeerConnection(configuration=RTC_CONFIG)
    print(f"📡 /publish: {publisher_id} 접속")
    publishers[publisher_id] = {"pc": pc, "track": None}

    @pc.on("connectionstatechange")
    async def on_state():
        print(f"publisher[{publisher_id}] state:", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            try:
                if publishers.get(publisher_id, {}).get("pc") is pc:
                    publishers.pop(publisher_id, None)
            finally:
                await pc.close()

    @pc.on("track")
    def on_track(track):
        print(f"퍼블리셔 트랙 수신: {publisher_id}, kind={track.kind}")
        if track.kind == "video":
            publishers[publisher_id]["track"] = track

        @track.on("ended")
        async def on_ended():
            if publishers.get(publisher_id, {}).get("track") is track:
                publishers[publisher_id]["track"] = None
            print(f"퍼블리셔 비디오 종료: {publisher_id}")

    await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

async def viewer(request: web.Request):
    """
    뷰어가 target(보고 싶은 publisher_id)와 SDP offer를 POST로 보냄
    -> 해당 퍼블리셔의 트랙을 relay하여 addTrack -> answer 반환
    바디: { "target": "cam01", "sdp": "...", "type": "offer" }
    """
    params = await request.json()
    target = params.get("target")
    if not target:
        return web.Response(status=400, text="target publisher_id is required")

    pub = publishers.get(target)
    if not pub or not pub.get("track"):
        return web.Response(status=503, text=f"No publisher track for {target}")

    pc = RTCPeerConnection(configuration=RTC_CONFIG)
    viewer_pcs.add(pc)
    print(f"👀 /viewer: target={target}")

    @pc.on("connectionstatechange")
    async def on_state():
        print("viewer state:", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            viewer_pcs.discard(pc)
            await pc.close()

    local_video = relay.subscribe(pub["track"])
    pc.addTrack(local_video)

    await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params["type"]))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response({"sdp": pc.localDescription.sdp, "type": pc.localDescription.type})

async def health(_):
    return web.Response(text="ok")

async def main_app():
    app = web.Application()
    app.router.add_get("/health", health)
    app.router.add_post("/publish", publish)
    app.router.add_post("/viewer", viewer)

    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods=["GET", "POST", "OPTIONS"],
        )
    })
    for r in list(app.router.routes()):
        try: cors.add(r)
        except: pass
    return app

if __name__ == "__main__":
    print("WebRTC Stream/Match Server running on http://0.0.0.0:8080")
    print("POST /publish  (publisher.py)")
    print("POST /viewer   (viewer.html)")
    web.run_app(main_app(), host="0.0.0.0", port=8080)
