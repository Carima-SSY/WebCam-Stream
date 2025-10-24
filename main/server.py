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

# publisher / viewer 관리 딕셔너리
publishers = {}
viewer_pcs = set()

# ================= Pydantic Models =================
class SDPOffer(BaseModel):
    sdp: str
    type: str

class PublishRequest(SDPOffer):
    publisher_id: str

class ViewerRequest(SDPOffer):
    target: str
# ===================================================

app = FastAPI()

# ========= CORS ============
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
# ===========================

@app.get("/health")
async def health():
    return {"status": "ok"}


# ===================================================
#                   PUBLISH
# ===================================================
@app.post("/publish")
async def publish(request: PublishRequest):
    publisher_id = request.publisher_id

    # 이전 publisher 세션이 남아 있다면 닫기
    if publisher_id in publishers:
        try:
            await publishers[publisher_id]["pc"].close()
        except Exception:
            pass
        publishers.pop(publisher_id, None)

    pc = RTCPeerConnection(configuration=RTC_CONFIG)
    print(f"/publish: Access to {publisher_id}")

    publishers[publisher_id] = {
        "pc": pc,
        "track": None,
        "original_track": None,
        "viewer_pc": None,
        "lock": asyncio.Lock()
    }
    pub = publishers[publisher_id]

    # connection state change
    # @pc.on("connectionstatechange")
    # async def on_state():
    #     print(f"publisher[{publisher_id}] state:", pc.connectionState)
    #     if pc.connectionState in ("failed", "closed", "disconnected"):
    #         if publishers.get(publisher_id, {}).get("pc") is pc:
    #             pub = publishers.pop(publisher_id, None)

    #         # viewer connection end 
    #         if pub:
    #             viewer_pc_to_close = pub.get("viewer_pc")
    #             if viewer_pc_to_close:
    #                 await viewer_pc_to_close.close()

    #             # clean relay track 
    #             if pub.get("track"):
    #                 try:
    #                     pub["track"].stop()
    #                 except Exception:
    #                     pass
    #                 pub["track"] = None
    #                 pub["original_track"] = None

    #         await pc.close()
    
    # publisher connection state call back
    @pc.on("connectionstatechange")
    async def on_state():
        print(f"publisher[{publisher_id}] state:", pc.connectionState)
        if pc.connectionState in ("failed", "closed", "disconnected"):
            if publishers.get(publisher_id, {}).get("pc") is pc:
                pub = publishers.pop(publisher_id, None)

            if pub:
                # viewer connection end
                viewer_pc_to_close = pub.get("viewer_pc")
                if viewer_pc_to_close:
                    await viewer_pc_to_close.close()

                # if publisher end, relay track absolute stop
                if pub.get("track"):
                    try:
                        pub["track"].stop()
                    except Exception:
                        pass
                    pub["track"] = None

                if pub.get("original_track"):
                    try:
                        pub["original_track"].stop()
                    except Exception:
                        pass
                    pub["original_track"] = None

            await pc.close()
    # Publisher 트랙 수신
    @pc.on("track")
    def on_track(track):
        print(f"Receive Publisher Track: {publisher_id}, kind={track.kind}")
        if track.kind == "video":
            pub["original_track"] = track  # save original track
            pub["track"] = relay.subscribe(track) # create relay pub track

        @track.on("ended")
        async def on_ended():
            # if pub.get("track") is track:
            #     pub["track"] = None
            print(f"End Publisher Video: {publisher_id}")
            pub["track"] = None
            pub["original_track"] = None

    # SDP 처리
    await pc.setRemoteDescription(RTCSessionDescription(sdp=request.sdp, type=request.type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


# ===================================================
#                   VIEWER
# ===================================================
@app.post("/viewer")
async def viewer(request: ViewerRequest):
    target = request.target
    pub = publishers.get(target)

    if not pub or not pub.get("track"):
        raise HTTPException(status_code=503, detail=f"No publisher track for {target}")

    async with pub["lock"]:
        if pub.get("viewer_pc") is not None:
            raise HTTPException(status_code=409, detail=f"Publisher {target} already has a viewer (1:1 limit).")

        pc = RTCPeerConnection(configuration=RTC_CONFIG)
        viewer_pcs.add(pc)
        pub["viewer_pc"] = pc

    print(f"/viewer: target={target}")

    @pc.on("connectionstatechange")
    async def on_state():
        import datetime
        print(f"[{datetime.datetime.now().time()}] viewer state: {pc.connectionState}")

        if pc.connectionState in ("failed", "closed", "disconnected"):
            viewer_pcs.discard(pc)
            async with pub["lock"]:
                if pub.get("viewer_pc") is pc:
                    pub["viewer_pc"] = None

                # if viewer end, clean relay track
                # if pub.get("track"):
                #     try:
                #         pub["track"].stop()
                #     except Exception:
                #         pass
                #     pub["track"] = None

            await pc.close()

    # create new relay pub track
    # if pub.get("original_track") is None:
    #     raise HTTPException(status_code=503, detail=f"No original track for {target}")

    # if viewer connect, create new relay pub track
    local_video = relay.subscribe(pub["original_track"])
    pub["track"] = local_video  # update new track
    pc.addTrack(local_video)

    # process SDP
    await pc.setRemoteDescription(RTCSessionDescription(sdp=request.sdp, type=request.type))
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


# ===================================================
#                   MANAGEMENT
# ===================================================
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


@app.post("/force_unlock/{target}")
async def force_unlock(target: str):    
    pub = publishers.get(target)
    if not pub:
        raise HTTPException(status_code=404, detail=f"Publisher {target} not found.")

    async with pub["lock"]:
        viewer_pc_to_close = pub.get("viewer_pc")
        if viewer_pc_to_close:
            await viewer_pc_to_close.close()
            viewer_pcs.discard(viewer_pc_to_close)

        pub["viewer_pc"] = None
        # if publisher alive, relay do not stop

    return {
        "status": "ok",
        "message": f"Viewer lock for {target} has been reset (relay preserved).",
    }

# ===================================================
#                   ENTRY POINT
# ===================================================
if __name__ == "__main__":
    import uvicorn
    print("WebRTC Stream/Match Server running on http://0.0.0.0:8080")
    uvicorn.run(app, host="0.0.0.0", port=8080)
