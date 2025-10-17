import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaRelay

# ===== ICE servers (coturn) =====
ICE_SERVERS = [
    RTCIceServer(urls=f"stun:<TURN_HOST>:3478"),
    RTCIceServer(urls=f"turn:<TURN_HOST>:3478?transport=udp", username="<TURN_USER>", credential="<TURN_PASS>"),
    RTCIceServer(urls=f"turn:<TURN_HOST>:3478?transport=tcp", username="<TURN_USER>", credential="<TURN_PASS>"),
]
RTC_CONFIG = RTCConfiguration(iceServers=ICE_SERVERS)
# =================================

relay = MediaRelay()
publishers = {}
viewer_pcs = set()

# Pydantic 모델 정의
class SDPOffer(BaseModel):
    sdp: str
    type: str

class PublishRequest(SDPOffer):
    publisher_id: str

class ViewerRequest(SDPOffer):
    target: str

app = FastAPI()

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/publish")
async def publish(request: PublishRequest):
    """
    퍼블리셔가 publisher_id와 SDP offer를 POST로 보냄
    """
    publisher_id = request.publisher_id
    if publisher_id in publishers:
        # 기존 연결 정리 또는 오류 처리
        await publishers[publisher_id]["pc"].close()

    pc = RTCPeerConnection(configuration=RTC_CONFIG)
    print(f"/publish: Access to {publisher_id}")
    publishers[publisher_id] = {"pc": pc, "track": None}

    # 연결 상태 변경 시 콜백
    @pc.on("connectionstatechange")
    async def on_state():
        print(f"publisher[{publisher_id}] state:", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            if publishers.get(publisher_id, {}).get("pc") is pc:
                publishers.pop(publisher_id, None)
            await pc.close()

    # Callback when receive track
    @pc.on("track")
    def on_track(track):
        print(f"Receive Publisher Track: {publisher_id}, kind={track.kind}")
        if track.kind == "video":
            publishers[publisher_id]["track"] = relay.subscribe(track) # Manage with Relay

        @track.on("ended")
        async def on_ended():
            if publishers.get(publisher_id, {}).get("track") is track:
                publishers[publisher_id]["track"] = None
            print(f"End Publisher Video: {publisher_id}")

    # SDP 처리
    await pc.setRemoteDescription(RTCSessionDescription(sdp=request.sdp, type=request.type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

@app.post("/viewer")
async def viewer_connect(request: ViewerRequest):
    target = request.target
    pub = publishers.get(target)
    
    # 퍼블리셔가 없거나 트랙이 없는 경우
    if not pub or not pub.get("track"):
        raise HTTPException(status_code=503, detail=f"No publisher track for {target}")

    pc = RTCPeerConnection(configuration=RTC_CONFIG)
    viewer_pcs.add(pc)
    print(f"/viewer: target={target}")

    # 연결 상태 변경 시 콜백
    @pc.on("connectionstatechange")
    async def on_state():
        print("viewer state:", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            viewer_pcs.discard(pc)
            await pc.close()

    # 퍼블리셔의 트랙을 relay를 통해 추가
    local_video = pub["track"]
    pc.addTrack(local_video)

    # SDP 처리
    await pc.setRemoteDescription(RTCSessionDescription(sdp=request.sdp, type=request.type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


if __name__ == "__main__":
    # uvicorn 서버 실행
    import uvicorn
    print("WebRTC Stream/Match Server running on http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)