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

# add viewer_pc field for configuration of publishers' 1:1 relation
publishers = {} 
viewer_pcs = set()

# Definition of Pydantic Model 
class SDPOffer(BaseModel):
    sdp: str
    type: str

class PublishRequest(SDPOffer):
    publisher_id: str

class ViewerRequest(SDPOffer):
    target: str

app = FastAPI()

# CORS Setting
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
    Publisher (Webcam) sends publisher_id and SDP offer with POST Method
    """
    publisher_id = request.publisher_id
    if publisher_id in publishers:
        # previous connection reset and process error
        await publishers[publisher_id]["pc"].close()

    pc = RTCPeerConnection(configuration=RTC_CONFIG)
    print(f"/publish: Access to {publisher_id}")
    # init viewer_pc for 1:1 connection limit
    publishers[publisher_id] = {"pc": pc, "track": None, "viewer_pc": None} 

    # call back when connection state is changed
    @pc.on("connectionstatechange")
    async def on_state():
        print(f"publisher[{publisher_id}] state:", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            # 퍼블리셔가 끊어지면, 연결된 뷰어도 함께 끊어야 함 (1:1 보장)
            pub_data = publishers.get(publisher_id, {})
            if pub_data.get("viewer_pc"):
                print(f"Closing viewer connection for {publisher_id}...")
                await pub_data["viewer_pc"].close() # 뷰어 연결 강제 종료
            
            if pub_data.get("pc") is pc:
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

    # Process SDP
    await pc.setRemoteDescription(RTCSessionDescription(sdp=request.sdp, type=request.type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

@app.post("/viewer")
async def viewer(request: ViewerRequest):
    target = request.target
    pub = publishers.get(target)
    
    # if no publisher or no track 
    if not pub or not pub.get("track"):
        raise HTTPException(status_code=503, detail=f"No publisher track for {target}")

    # limitation if 1:1 connection: check activated connection
    if pub.get("viewer_pc") is not None:
        raise HTTPException(status_code=409, detail=f"Publisher {target} is already connected to a viewer (1:1 limit).")

    pc = RTCPeerConnection(configuration=RTC_CONFIG)
    viewer_pcs.add(pc)
    print(f"/viewer: target={target}")

    # save viewer pc object in pub dict
    pub["viewer_pc"] = pc 

    # call back when connection state change 
    @pc.on("connectionstatechange")
    async def on_state():
        print("viewer state:", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            # if viewer is disconnected, set pub["viewer_pc"] = None to allow other connection
            if pub.get("viewer_pc") is pc:
                pub["viewer_pc"] = None 
            viewer_pcs.discard(pc)
            await pc.close()

    # add publisher track with relay
    local_video = pub["track"]
    pc.addTrack(local_video)

    # process SDP
    await pc.setRemoteDescription(RTCSessionDescription(sdp=request.sdp, type=request.type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


# In a production/deployment environment, comment out the code below
if __name__ == "__main__":
    # execute uvicorn
    import uvicorn
    print("WebRTC Stream/Match Server running on http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)