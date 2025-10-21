import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaRelay

# ===== ICE servers (coturn) =====
# TODO: <TURN_HOST>, <TURN_USER>, <TURN_PASS> 부분을 실제 정보로 변경해야 합니다.
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

# ===== CORS 설정 수정 =====
# OOM-Kill 해결을 위해 명시적인 Origin 주소를 사용합니다.
origins = [
    "http://localhost:5173",  # React 개발 환경 등 클라이언트 Origin
    "http://localhost:3000",
    # 실제 배포 도메인이 있다면 여기에 추가하세요: "https://your.domain.com"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,       # ✨ 수정: 명시적 Origin 사용
    allow_credentials=True,      # Credentials 허용 유지
    allow_methods=["*"],
    allow_headers=["*"],
)
# ==========================

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
    
    # ✨ Lock 객체를 생성하여 publishers 딕셔너리에 함께 저장합니다.
    publishers[publisher_id] = {
        "pc": pc, 
        "track": None,
        "viewer_pc": None, # 1:1 연결을 위한 뷰어 PC 객체 저장 공간
        "lock": asyncio.Lock() # ✨ 새로운 Lock 객체 추가 (메모리 누수 방지 핵심)
    }
    pub = publishers[publisher_id]

    # 연결 상태 변경 시 콜백
    @pc.on("connectionstatechange")
    async def on_state():
        print(f"publisher[{publisher_id}] state:", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            if publishers.get(publisher_id, {}).get("pc") is pc:
                publishers.pop(publisher_id, None)
            
            # ✨ Publisher 종료 시 해당 스트림을 보고 있던 viewer PC도 정리합니다.
            viewer_pc_to_close = pub.get("viewer_pc")
            if viewer_pc_to_close:
                await viewer_pc_to_close.close()

            await pc.close()

    # Callback when receive track
    @pc.on("track")
    def on_track(track):
        print(f"Receive Publisher Track: {publisher_id}, kind={track.kind}")
        if track.kind == "video":
            pub["track"] = relay.subscribe(track) # Manage with Relay

        @track.on("ended")
        async def on_ended():
            if pub.get("track") is track:
                pub["track"] = None
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

    # ✨ [수정 시작] Lock을 사용하여 1:1 연결 상태를 보호합니다.
    async with pub["lock"]: 
        # 1:1 연결 제한 로직 추가
        if pub.get("viewer_pc") is not None:
            raise HTTPException(status_code=409, detail=f"Publisher {target} is already connected to a viewer (1:1 limit).")
            
        pc = RTCPeerConnection(configuration=RTC_CONFIG)
        viewer_pcs.add(pc)
        pub["viewer_pc"] = pc # ✨ 락 안에서 새로운 pc 객체 저장 (안전 확보)

    # 락이 해제된 후 로그 출력 및 트랙 추가 진행
    print(f"/viewer: target={target}")
    # [수정 끝]

    # 연결 상태 변경 시 콜백
    @pc.on("connectionstatechange")
    async def on_state():
        print("viewer state:", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            viewer_pcs.discard(pc)
            
            # ✨ [수정 시작] 락을 획득하여 안전하게 상태 초기화 (메모리 누수 방지)
            async with pub["lock"]: 
                if pub.get("viewer_pc") is pc:
                    pub["viewer_pc"] = None # ✨ pub의 viewer_pc 상태를 None으로 초기화
            # [수정 끝]
            
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