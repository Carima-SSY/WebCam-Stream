import asyncio
from aiohttp import web
import aiohttp_cors
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from av import VideoFrame
import numpy as np
import cv2  # OpenCV

# 더미 비디오 스트림 트랙
class DummyVideoStreamTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self.counter = 0

    async def recv(self):
        # 30 FPS
        await asyncio.sleep(1.0 / 30)
        try:
            # 640x480 검은색 프레임
            frame = np.zeros((480, 640, 3), dtype=np.uint8)

            # 카운터 텍스트
            cv2.putText(
                frame,
                str(self.counter),
                (50, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (255, 255, 255),
                2,
                cv2.LINE_AA
            )
            self.counter += 1

            new_frame = VideoFrame.from_ndarray(frame, format="bgr24")
            # 타임스탬프 설정
            pts, time_base = await self.next_timestamp()
            new_frame.pts = pts
            new_frame.time_base = time_base
            return new_frame
        except Exception as e:
            print("DummyVideoStreamTrack error:", e)
            raise
# /offer 핸들러
async def offer(request):
    params = await request.json()
    pc = RTCPeerConnection()

    # 1) 원격 SDP 먼저 반영
    remote_sdp = params["sdp"]
    remote_type = params.get("type", "offer")
    
    print(f">>>>SDP: {remote_sdp}")
    print(f">>>>TYPE: {remote_type}")
    
    await pc.setRemoteDescription(RTCSessionDescription(remote_sdp, remote_type))

    # 2) 원격이 제시한 m-line(트랜시버)만 조정
    video_transceivers = [t for t in pc.getTransceivers() if t.kind == "video"]
    audio_transceivers = [t for t in pc.getTransceivers() if t.kind == "audio"]

    # 오디오는 로컬 트랙이 없으므로 비활성화(선택)
    for t in audio_transceivers:
        t.direction = "inactive"

    # 원격 Offer에 video m-line이 있는 경우에만 더미 트랙 연결
    if video_transceivers:
        vt = video_transceivers[0]
        vt.direction = "sendonly"  # 서버는 비디오 송신만
        # ✅ replaceTrack 은 동기 함수 -> await 사용 금지
        vt.sender.replaceTrack(DummyVideoStreamTrack())

    # 3) Answer 생성/적용
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    print(">>>>END OFFER FUNC")
    return web.json_response({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    })

async def main_app():
    app = web.Application()

    # CORS 설정 (OPTIONS 포함)
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods=["GET", "POST", "OPTIONS"],
        )
    })

    # 라우트 등록 후 CORS 적용
    offer_route = app.router.add_post("/offer", offer)
    cors.add(offer_route)

    return app

if __name__ == "__main__":
    print("WebRTC P2P Server is running on http://127.0.0.1:8080")
    print("client.html을 브라우저에서 열어 '스트리밍 시작'을 누르세요.")
    web.run_app(main_app(), host="0.0.0.0", port=8080)
