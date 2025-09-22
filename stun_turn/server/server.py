import asyncio
from aiohttp import web
import aiohttp_cors
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer  # ✅ 웹캠/마이크 입력
import platform

# ---- 카메라 플레이어 생성 (운영체제별) -----------------
def create_camera_player():
    system = platform.system().lower()

    # macOS (avfoundation)
    # 기본 카메라: "default"
    # 참고: 오디오 없이 비디오만 사용 (mic 불필요 시)
    if "darwin" in system or "mac" in system:
        return MediaPlayer(
            "default",                      # 기본 카메라
            format="avfoundation",
            options={"framerate": "30", "video_size": "640x480"},
        )

    # Linux (v4l2)
    # 장치 경로 예: /dev/video0
    if "linux" in system:
        return MediaPlayer(
            "/dev/video0",
            format="v4l2",
            options={"framerate": "30", "video_size": "640x480"},
        )

    # Windows (dshow)
    # 장치 이름은 장치 관리자/카메라 앱에서 확인 후 문자열로 넣기
    if "windows" in system:
        # 예: "video=Integrated Camera"
        return MediaPlayer(
            "video=Integrated Camera",
            format="dshow",
            options={"video_size": "640x480", "framerate": "30"},
        )

    # 기본값: 시도 실패 시 None
    return None
# -------------------------------------------------------

async def offer(request):
    params = await request.json()
    pc = RTCPeerConnection()

    # 1) 원격 SDP 먼저 적용
    remote_sdp = params["sdp"]
    remote_type = params.get("type", "offer")
    await pc.setRemoteDescription(RTCSessionDescription(remote_sdp, remote_type))

    # 2) 원격이 제시한 m-line(트랜시버)만 사용
    video_transceivers = [t for t in pc.getTransceivers() if t.kind == "video"]
    audio_transceivers = [t for t in pc.getTransceivers() if t.kind == "audio"]

    # 오디오는 로컬 트랙이 없으므로 비활성화(필요 시 그대로 두어도 무방)
    for t in audio_transceivers:
        t.direction = "inactive"

    # 클라이언트 Offer에 video m-line이 있을 때만 카메라 송신
    if video_transceivers:
        vt = video_transceivers[0]
        vt.direction = "sendonly"  # 서버는 비디오 송신만

        player = create_camera_player()
        if player is None or player.video is None:
            # 카메라 열기 실패 시 500 반환
            return web.Response(status=500, text="Failed to open camera device")

        # replaceTrack는 동기 함수 (await 금지)
        vt.sender.replaceTrack(player.video)
    else:
        # video m-line이 없으면 400 (클라에서 recvonly 트랜시버 추가 필요)
        return web.Response(status=400, text="Client offer has no video m-line")

    # 3) Answer 생성/적용
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return web.json_response({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    })

async def main_app():
    app = web.Application()

    # CORS (핸들러에서 수동 헤더 추가 금지)
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods=["GET", "POST", "OPTIONS"],
        )
    })

    offer_route = app.router.add_post("/offer", offer)
    cors.add(offer_route)

    return app

if __name__ == "__main__":
    print("WebRTC P2P Server is running on http://127.0.0.1:8080")
    print("client.html을 브라우저에서 열어 '스트리밍 시작'을 누르세요.")
    web.run_app(main_app(), host="0.0.0.0", port=8080)
