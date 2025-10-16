import cv2

def find_webcam_indices(max_index_to_check=5):
    """시스템에 연결된 웹캠 인덱스를 찾아 반환합니다."""
    working_indices = []
    print("웹캠 인덱스를 탐색 중입니다...")

    for i in range(max_index_to_check):
        cap = cv2.VideoCapture(i)
        
        if cap.isOpened():
            print(f"✅ 인덱스 {i}번: 정상적으로 인식되었습니다.")
            working_indices.append(i)
            cap.release()
        else:
            print(f"❌ 인덱스 {i}번: 카메라가 없습니다.")

    return working_indices

if __name__ == "__main__":
    found_indices = find_webcam_indices()
    
    if found_indices:
        print("\n--- 탐색 결과 ---")
        print(f"작동하는 웹캠 인덱스: {found_indices}")
        print("이 인덱스 번호로 코드를 수정해 주세요.")
    else:
        print("\n작동하는 웹캠을 찾을 수 없습니다. 연결 상태를 확인해주세요.")