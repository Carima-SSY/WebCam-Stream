import asyncio
import logging
import boto3
import json
import ssl
import threading
import sys
from datetime import datetime, UTC
from urllib.parse import urlparse, urlencode, quote
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCIceCandidate
from aiortc.contrib.media import MediaPlayer
from websocket import WebSocketApp
import hmac
import hashlib

# ----------------------------------------------------------------------
# 1. 설정 (사용자 정보로 변경 필요)
# ----------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)

KVS_CHANNEL_NAME = "carima-hub_kvs_webrtc_test"  # ★★★ 채널 이름으로 변경 필수 ★★★
AWS_REGION = "ap-northeast-2"

# macOS 웹캠 설정: '0' (내장 카메라) 혹은 연결된 USB 카메라 인덱스
WEBCAM_DEVICE = "0"
WEBCAM_FORMAT = "avfoundation" 
# ----------------------------------------------------------------------

# 전역 변수
ws = None
pc = None
loop = asyncio.get_event_loop()

# ----------------------------------------------------------------------
# 2. AWS WebSocket 인증 (SigV4) - 최종 보강된 로직
# ----------------------------------------------------------------------
def sign_ws_url(url, access_key, secret_key, region, service, session_token=None):
    """SigV4 규약에 맞춰 WebSocket URL을 서명하고 반환합니다."""
    
    t = datetime.now(UTC)
    amz_date = t.strftime('%Y%m%dT%H%M%SZ')
    date_stamp = t.strftime('%Y%m%d')
    
    parsed_url = urlparse(url)
    
    # SigV4 규약을 위해 슬래시와 콜론은 인코딩하지 않도록 설정
    def kvs_quote_via(value, safe='/', encoding=None, errors=None):
        # urlencode가 전달하는 기본 safe 문자에 KVS에 필요한 문자를 추가합니다.
        kvs_safe_chars = safe + ':/' 
        return quote(value, safe=kvs_safe_chars, encoding=encoding, errors=errors)

    # 2.1. Canonical Query String 생성 및 정렬
    query_params = {
        'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
        'X-Amz-Credential': f"{access_key}/{date_stamp}/{region}/{service}/aws4_request",
        'X-Amz-Date': amz_date,
        'X-Amz-SignedHeaders': 'host', 
    }
    
    signed_headers_string = "host"
    canonical_headers = f"host:{parsed_url.hostname.lower()}\n"

    # 세션 토큰이 있다면 추가
    if session_token:
        query_params['X-Amz-Security-Token'] = session_token
        signed_headers_string += ";x-amz-security-token"
        canonical_headers += f"x-amz-security-token:{session_token}\n"
    
    # urlencode를 사용하여 쿼리 파라미터를 정렬하고 인코딩합니다. (오류 해결)
    canonical_querystring = urlencode(query_params, quote_via=kvs_quote_via)

    # 2.2. Canonical Request 구성
    canonical_request = '\n'.join([
        'GET',
        '/', # Canonical URI
        canonical_querystring,
        canonical_headers,  # 'host:hostname\n'
        '',                 # 필수적인 빈 줄
        signed_headers_string,
        hashlib.sha256(''.encode('utf-8')).hexdigest()
    ])
    
    # 2.3. String to Sign 생성
    string_to_sign = '\n'.join([
        'AWS4-HMAC-SHA256',
        amz_date,
        f"{date_stamp}/{region}/{service}/aws4_request",
        hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()
    ])
    
    # 2.4. 서명(Signature) 생성
    def get_signature_key(key, date_stamp, region, service):
        k_date = hmac.new(f"AWS4{key}".encode('utf-8'), date_stamp.encode('utf-8'), hashlib.sha256).digest()
        k_region = hmac.new(k_date, region.encode('utf-8'), hashlib.sha256).digest()
        k_service = hmac.new(k_region, service.encode('utf-8'), hashlib.sha256).digest()
        k_signing = hmac.new(k_service, "aws4_request".encode('utf-8'), hashlib.sha256).digest()
        return k_signing

    signing_key = get_signature_key(secret_key, date_stamp, region, service)
    signature = hmac.new(signing_key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    
    # 2.5. 최종 서명된 URL 반환
    return f"{url}?{canonical_querystring}&X-Amz-Signature={signature}"

# ----------------------------------------------------------------------
# 3. WebSocket 및 WebRTC 로직
# ----------------------------------------------------------------------

def on_message(ws, message):
    """WebSocket에서 메시지를 받았을 때 호출"""
    msg = json.loads(message)
    msg_type = msg.get('messageType')
    
    if msg_type == 'ANSWER':
        sdp_offer_answer = msg.get('sdpOffer')
        print("-> Received ANSWER. Setting remote description...")
        asyncio.run_coroutine_threadsafe(pc.setRemoteDescription(
            RTCSessionDescription(sdp=sdp_offer_answer['sdp'], type='answer')
        ), loop)
    elif msg_type == 'ICE_CANDIDATE':
        ice_candidate = msg.get('iceCandidate')
        if ice_candidate:
            print("-> Received ICE Candidate.")
            # 상대방의 ICE 후보자를 PeerConnection에 추가
            candidate_str = ice_candidate.get('candidate')
            if candidate_str: # candidate 문자열이 있는지 확인
                asyncio.run_coroutine_threadsafe(pc.addIceCandidate(
                    RTCIceCandidate(
                        sdpMid=ice_candidate['sdpMid'],
                        sdpMLineIndex=ice_candidate['sdpMLineIndex'],
                        candidate=candidate_str
                    )
                ), loop)

def on_open(ws):
    print("WebSocket connection opened successfully.")

def on_error(ws, error):
    print(f"WebSocket Error: {error}")

def send_to_websocket(msg):
    """WebRTC 메시지를 KVS 시그널링 채널로 보냅니다."""
    if ws and ws.sock and ws.sock.connected: # 연결 상태 확인
        ws.send(json.dumps(msg))

async def run_master():
    global ws, pc, loop
    
    kvs_client = boto3.client('kinesisvideo', region_name=AWS_REGION)
    credentials = boto3.Session().get_credentials()
    
    # 1. ChannelName으로 ChannelARN 조회
    try:
        response = kvs_client.describe_signaling_channel(ChannelName=KVS_CHANNEL_NAME)
        channel_arn = response['ChannelInfo']['ChannelARN']
    except Exception as e:
        print(f"Error describing channel {KVS_CHANNEL_NAME}: {e}")
        return

    # 2. ChannelARN을 사용하여 엔드포인트 가져오기
    response = kvs_client.get_signaling_channel_endpoint(
        ChannelARN=channel_arn,
        SingleMasterChannelEndpointConfiguration={'Protocols': ['WSS'], 'Role': 'MASTER'}
    )
    endpoint = response['ResourceEndpointList'][0]['ResourceEndpoint']
    
    # 3. SigV4 서명된 WebSocket URL 생성
    signed_url = sign_ws_url(
        url=endpoint,
        access_key=credentials.access_key,
        secret_key=credentials.secret_key,
        session_token=credentials.token, 
        region=AWS_REGION,
        service='kinesisvideo'
    )

    # 4. WebSocket 연결 시작
    ws_thread = threading.Thread(target=lambda: WebSocketApp(
        signed_url,
        on_message=on_message, 
        on_error=on_error, 
        on_close=lambda ws, close_status_code, close_msg: print(f"WebSocket closed: {close_status_code}, {close_msg}"),
        on_open=on_open,
        # create_connection 대신 WebSocketApp의 run_forever 사용
    ).run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}), daemon=True)
    ws_thread.start()
    
    print("WebSocket thread started. Waiting for connection...")
    
    # WebSocket 객체가 초기화될 때까지 잠시 대기
    await asyncio.sleep(2)
    
    # 5. WebRTC PeerConnection 생성 및 핸들러 정의
    pc = RTCPeerConnection()

    @pc.on("icecandidate")
    async def on_icecandidate(candidate):
        if candidate and candidate.sdpMid:
            print("-> Sending ICE Candidate.")
            send_to_websocket({
                'action': 'ICE_CANDIDATE',
                'iceCandidate': {
                    'candidate': candidate.candidate,
                    'sdpMid': candidate.sdpMid,
                    'sdpMLineIndex': candidate.sdpMLineIndex
                }
            })

    @pc.on("negotiationneeded")
    async def on_negotiationneeded():
        """Offer 생성 및 KVS 시그널링 채널로 전송"""
        await pc.setLocalDescription(await pc.createOffer())
        print("-> Sending OFFER.")
        send_to_websocket({
            'action': 'OFFER',
            'sdpOffer': {
                'sdp': pc.localDescription.sdp,
                'type': pc.localDescription.type
            }
        })
    
    # 6. 웹캠 소스 추가 (macOS용 설정)
    try:
        # macOS: format="avfoundation", device="0" (내장 카메라)
        player = MediaPlayer("default", format="avfoundation", options={"framerate": "30", "video_size": "640x480"})
        pc.addTrack(player.video)
    except Exception as e:
        print(f"Error adding media track: {e}")
        print("Check WEBCAM_DEVICE path and ensure camera permissions are granted.")
        return

    print("WebRTC Signaling started. Waiting for viewer...")
    
    # 연결 유지
    try:
        await pc.wait_for_connection_state("closed")
    except asyncio.CancelledError:
        pass
    finally:
        if pc:
            await pc.close()

if __name__ == "__main__":
    def signal_handler(sig, frame):
        print("\nShutting down application...")
        if ws and ws.sock and ws.sock.connected:
            ws.close()
        sys.exit(0)

    import signal
    signal.signal(signal.SIGINT, signal_handler)
    
    try:
        asyncio.run(run_master())
    except KeyboardInterrupt:
        pass