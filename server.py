import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.media import MediaRelay

# ===== ICE servers (coturn) =====
ICE_SERVERS = [
    RTCIceServer(urls=f"stun:3.34.163.192:3478"),
    RTCIceServer(urls=f"turn:3.34.163.192:3478?transport=udp", username="webrtcuser", credential="webrtcpass"),
    RTCIceServer(urls=f"turn:3.34.163.192:3478?transport=tcp", username="webrtcuser", credential="webrtcpass"),
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

# ========= CORS Setting ============
# Set Origins 
origins = [
    "http://localhost:5173",  
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ===================================


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
    # add Lock object and save to dict
    publishers[publisher_id] = {
        "pc": pc, 
        "track": None,
        "viewer_pc": None, 
        "lock": asyncio.Lock()
    } 
    pub = publishers[publisher_id]
    
    # call back when connection state is changed
    @pc.on("connectionstatechange")
    async def on_state():
        print(f"publisher[{publisher_id}] state:", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            if publishers.get(publisher_id, {}).get("pc") is pc:
                publishers.pop(publisher_id, None)
                
            # when Publisher end, clean viewer PC
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

    # using Lock for maintaining 1:1 connection state
    async with pub["lock"]: 
        # limitation if 1:1 connection: check activated connection
        if pub.get("viewer_pc") is not None:
            raise HTTPException(status_code=409, detail=f"Publisher {target} is already connected to a viewer (1:1 limit).")
            
        pc = RTCPeerConnection(configuration=RTC_CONFIG)
        viewer_pcs.add(pc)
        pub["viewer_pc"] = pc # save new pc object (safety)

    # pc = RTCPeerConnection(configuration=RTC_CONFIG)
    # viewer_pcs.add(pc)
    print(f"/viewer: target={target}")

    # save viewer pc object in pub dict
    # pub["viewer_pc"] = pc 

    # call back when connection state change 
    @pc.on("connectionstatechange")
    async def on_state():
        import datetime
        print(f"[{datetime.datetime.now().time()}] viewer state: {pc.connectionState}")
        if pc.connectionState in ("failed", "closed", "disconnected"):
            viewer_pcs.discard(pc)
            
            # init state safely by accepting lock (because of Memory Leak)
            async with pub["lock"]: 
                if pub.get("viewer_pc") is pc:
                    pub["viewer_pc"] = None # init pub's viewer_pc state to None
            await pc.close()

    # add publisher track with relay
    local_video = pub["track"]
    pc.addTrack(local_video)

    # process SDP
    await pc.setRemoteDescription(RTCSessionDescription(sdp=request.sdp, type=request.type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}

@app.get("/viewers_count")
async def viewers_count():
    return {"count": len(viewer_pcs)}

@app.get("/viewers_detail")
async def viewers_detail():
    details = []
    for pc in viewer_pcs:
        details.append({
            "pc_id": id(pc), 
            "connection_state": pc.connectionState,
            "is_tracked_by_publisher": any(
                pub.get("viewer_pc") is pc 
                for pub in publishers.values()
            )
        })
    return {"connections": details}

# In a production/deployment environment, comment out the code below
#if __name__ == "__main__":
#    # execute uvicorn
#    import uvicorn
#    print("WebRTC Stream/Match Server running on http://0.0.0.0:8080")
#    uvicorn.run(app, host="0.0.0.0", port=8080)
