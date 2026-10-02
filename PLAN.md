# CCTV 실시간 얼굴 검색 및 Person Tracking 시스템 개발

Python 기반 CCTV 실시간 얼굴 검색 시스템을 개발해줘.
목표는 여러 CCTV의 RTSP 영상을 실시간 분석하여 사용자가 등록한 특정 인물을 얼굴로 검색하고, 발견된 사람을 같은 카메라에서 추적하며 검색 결과를 웹 UI에 실시간 표시하는 것이다.
향후 여러 CCTV 사이의 Person Re-ID와 이동경로 추적 기능을 추가할 수 있도록 확장 가능한 구조로 설계한다.
이 프로젝트는 비상업 시험용 프로젝트다.
시험에 적합한 공개 pretrained model을 우선 사용하고, 코드와 모델 가중치 각각의 사용 조건을 확인한다.
중요:

- Docker를 사용하지 않는다.
- Ubuntu에 필요한 서비스를 native 방식으로 설치하고 실행한다.
- Python은 venv를 사용한다.
- GPU/CUDA를 직접 사용한다.
- 개발 중 각 Phase가 끝날 때 실제 실행 가능한 상태를 유지한다.
- placeholder 코드만 대량 생성하지 않는다.

---

# 1. 개발 환경

Target OS:
Ubuntu 24.04
GPU:
NVIDIA RTX 3070 Ti
VRAM 8GB
CUDA 사용
Python:
Python 3.11+
venv 사용
Backend:
FastAPI
Uvicorn
SQLAlchemy 2.x
Alembic
Frontend:
Angular
Bootstrap
Database:
MariaDB
Vector database:
Qdrant
Video:
FFmpeg
OpenCV
RTSP
AI:
Person Detection:
YOLO 계열
Tracking:
ByteTrack 또는 BoT-SORT
Face Detection:
InsightFace에서 사용할 수 있는 검증된 face detector
Face Recognition:
InsightFace / ArcFace
Person Re-ID:
별도 모듈로 설계
MVP에서는 optional
가능하면 pretrained model을 사용한다.
AI component는 특정 라이브러리에 강하게 결합하지 말고 interface/adapter 구조로 만든다.
모델 선정 및 라이선스:

- 비상업 시험용 목적에 맞는 코드와 모델 가중치를 선정한다. 상업용 라이선스 구매를 기본 전제로 삼지 않는다.
- InsightFace Python 코드의 MIT License와 공식 pretrained model의 비상업 연구용 조건을 구분한다. 실제 시험 목적이 선택한 모델의 허용 범위에 해당하는지 확인한다.
- Ultralytics 구현을 선택하면 비상업 시험용에서도 적용되는 AGPL-3.0 조건을 확인한다. YOLO 계열 전체가 동일한 라이선스를 가진다고 가정하지 않는다.
- 선택한 구현, 모델명, 버전, 다운로드 출처, 가중치 checksum, 사용 조건을 docs/models.md에 기록한다.
- 상업 운영으로 목적이 바뀌면 사용 조건을 다시 검토한다.

참고:

- [InsightFace 모델 사용 조건](https://github.com/deepinsight/insightface/blob/master/python-package/docs/model_zoo.md)
- [Ultralytics 라이선스](https://www.ultralytics.com/license)

---

# 2. 프로젝트 구조

다음 구조를 기본으로 사용한다.
현재 repository root를 프로젝트 root로 사용한다.

```text
backend/
    app/
        api/
        core/
        db/
        models/
        schemas/
        services/
        video/
            capture/
            buffering/
        ai/
            detectors/
            trackers/
            face/
            reid/
        vector/
        events/
        workers/
        websocket/
        storage/
    tests/
frontend/
scripts/
config/
deploy/
    systemd/
    nginx/
data/
    persons/
    faces/
    events/
    clips/
logs/
docs/
requirements.txt
requirements.lock
.env.example
.gitignore
README.md
```

디렉터리와 모듈은 해당 Phase에서 필요한 것부터 생성한다.
후속 Phase의 기능은 architecture 문서에 설계하고, 실행되지 않는 placeholder 구현을 대량 생성하지 않는다.

---

# 3. Python 환경

Docker를 사용하지 않는다.
Python virtual environment를 사용한다.
설치 예:
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
requirements.txt에는 필요한 Python dependency를 명시한다.
CUDA/PyTorch/ONNX Runtime GPU compatibility를 확인한다.
Python minor version, PyTorch 및 CUDA build, ONNX Runtime GPU, CUDA runtime, cuDNN의 검증된 조합을 docs/architecture.md와 README에 기록한다.
현재 설치된 Python 버전으로 호환성을 확인하고, 다른 버전이 필요하면 이유와 설치 방법을 명시한다.
검증한 dependency를 requirements.txt와 requirements.lock에 고정하고, GPU wheel에 별도 package index가 필요하면 설치 명령에 명시한다.
nvidia-smi의 CUDA 표시는 드라이버가 지원하는 버전 정보이므로, 실제 로딩된 CUDA runtime 및 cuDNN 버전과 구분해서 보고한다.
GPU toolkit이나 드라이버를 변경하기 전에 기존 설치와 선택한 wheel의 요구 조건을 확인한다.
GPU가 정상적으로 사용되는지 확인하는 script를 만든다.
scripts/check_gpu.py
출력 예:
CUDA available: true
GPU:
NVIDIA GeForce RTX 3070 Ti
VRAM:
8192 MB
PyTorch CUDA:
available
ONNX Runtime providers:
CUDAExecutionProvider
CPUExecutionProvider
검증 범위:

- GPU와 driver 조회, PyTorch CUDA tensor 연산을 실제 실행한다.
- ONNX Runtime provider 목록과 실제 생성한 session의 provider를 각각 확인한다.
- Phase 1에서는 작은 ONNX smoke model로 CUDA session 생성 및 추론을 실행하고, profiling 등으로 CUDA 실행 증거를 확인한다.
- Phase 2~3에서는 선택한 YOLO 및 얼굴 모델로 실제 CUDA 추론을 다시 확인한다.
- provider가 목록에 있다는 이유만으로 GPU 추론 성공으로 판정하지 않는다.

GPU가 없을 경우 가능하면 CPU fallback을 지원한다.
CPU fallback 여부는 설정으로 제어하고, 실행 장치와 fallback 원인을 상태 API 및 검증 결과에 표시한다.
GPU를 요청한 실행에서 CUDA가 실패하면 이를 명시적으로 보고한다. CPU 실행으로 GPU 검증을 통과 처리하지 않는다.
참고: [ONNX Runtime CUDA 요구 조건](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html)

---

# 4. CCTV 관리

CCTV를 등록할 수 있어야 한다.
Camera:
camera_id
name
description
rtsp_url
location
enabled
created_at
updated_at
API:
GET /api/cameras
POST /api/cameras
PUT /api/cameras/{camera_id}
DELETE /api/cameras/{camera_id}
POST /api/cameras/{camera_id}/start
POST /api/cameras/{camera_id}/stop
GET /api/cameras/{camera_id}/status
RTSP 연결이 끊어질 경우 자동 reconnect한다.
RTSP username/password는 로그에 출력하지 않는다.
재연결 및 worker 재시작으로 새로운 추적 구간이 시작될 때 stream_session_id를 새로 생성한다.
카메라 상태에는 stream_session_id, 실행 장치, 마지막 frame 시각, reconnect 상태를 포함한다.

---

# 5. Video Processing

각 CCTV stream은 독립적으로 관리한다.
독립 관리의 대상은 capture, latest-frame buffer, tracker, reconnect 상태다.
초기 GPU inference worker는 단일 프로세스로 두고, detector와 face model session을 모델별로 한 번만 로딩하여 카메라 사이에서 공유한다.
카메라별로 GPU 모델을 중복 로딩하지 않는다. GPU inference 요청은 카메라별 처리 기회를 보장하는 bounded scheduler로 실행한다.
FastAPI API 프로세스는 GPU 모델과 RTSP capture를 소유하지 않는다.
Uvicorn worker 수나 reload 때문에 inference worker가 중복 시작되지 않도록 API와 worker의 실행 명령 및 lifecycle을 분리한다.
API와 worker 통신:

- MVP에서는 localhost 전용 내부 HTTP를 사용하고 service token으로 인증한다.
- API는 camera start/stop 및 등록 인물 cache 갱신 명령을 worker에 전달한다. worker는 상태 조회 API를 제공한다.
- worker는 MatchEvent를 MariaDB에 먼저 저장하고, event_id를 API 내부 endpoint로 전달하여 WebSocket 배포를 요청한다.
- 알림 실패 시 bounded retry를 수행한다. DB가 이벤트 기록의 기준이며, 누락된 알림은 event_id 기준 조회로 복구한다.
- 명령 성공은 worker의 응답을 확인한 뒤 보고한다. worker 연결 실패를 성공으로 처리하지 않는다.
- 초기 API는 단일 Uvicorn 프로세스로 운영한다. 향후 여러 API 프로세스를 사용하려면 이벤트 배포를 모든 프로세스에 전달하는 구조를 별도로 구현한다.

Pipeline:
RTSP
↓
Frame Capture
↓
Person Detection
↓
Tracking
↓
Face Detection
↓
Face Quality Check
↓
Face Alignment
↓
Face Embedding
↓
Target Search
↓
Match Event
RTX 3070 Ti 8GB를 고려하여 GPU memory 사용을 최소화한다.
추론과 영상 encoding 같은 blocking 작업을 API의 async event loop에서 직접 실행하지 않는다.

---

# 6. 실시간 처리 전략

원본 CCTV가 30fps여도 모든 frame에서 AI inference를 하지 않는다.
기본값:
DETECTION_FPS=5
FACE_ANALYSIS_INTERVAL=0.5
DETECTION_CONFIDENCE=0.1
TRACK_LOW_THRESHOLD=0.1
TRACK_HIGH_THRESHOLD=0.5
NEW_TRACK_THRESHOLD=0.6
FACE_MIN_SIZE=80
FACE_QUALITY_THRESHOLD=0.7
위 값은 초기 시험값이며 선택한 detector/tracker 설정에 맞춰 검증한다.
DETECTION_CONFIDENCE는 tracker에 전달할 검출의 최소 점수다. 표시 및 새 track 생성에 사용하는 높은 threshold와 구분한다.
ByteTrack을 선택하면 낮은 점수의 검출을 detector 단계에서 제거하여 후속 association에 사용하지 못하게 하지 않는다.
동일 track에 대해 매 frame 얼굴 embedding을 생성하지 않는다.
좋은 얼굴이 발견됐을 때만 ArcFace embedding을 생성한다.
실시간 처리에서는 오래된 frame보다 최신 frame을 우선한다.
queue가 밀리면 오래된 frame을 drop한다.
bounded queue 또는 latest-frame 전략을 사용한다.
카메라별 inference 대기 frame은 기본 1개로 제한한다. 다른 크기를 사용할 때는 상한을 설정한다.
각 frame 및 분석 결과에 stream_session_id, frame_id, capture timestamp를 포함한다.
저장 및 화면 표시 시각은 UTC로 기록하고, 처리 시간과 timeout 측정에는 monotonic clock을 사용한다.

---

# 7. Person Detection

YOLO pretrained model을 사용한다.
person class만 detection한다.
출력:
bbox
confidence
Detector interface:

```python
class PersonDetector:
    def detect(self, frame):
        ...
```

향후 다른 detector로 교체할 수 있도록 구현한다.

---

# 8. Tracking

ByteTrack 또는 BoT-SORT를 사용한다.
각 사람에게 track_id를 부여한다.
Track:
track_id
camera_id
stream_session_id
bbox
first_seen
last_seen
best_face
best_face_quality
face_embedding
person_embedding
identity_candidate
identity_score
Tracking 자체는 가능한 한 얼굴 인식과 독립적으로 동작하게 한다.
track_id의 유효 범위는 camera_id와 stream_session_id 내부다. DB의 track 기본키는 별도의 UUID 또는 surrogate key를 사용한다.
실제 tracker update 빈도와 frame 사이의 경과 시간을 반영한다. 원본 30 FPS 기준 설정을 5 FPS 분석에 그대로 적용하지 않는다.
lost-track 유지 시간과 identity 만료 시간을 초 단위로 정의하고, tracker의 frame 기반 설정이 필요하면 실제 분석 FPS에 맞춰 변환한다.
stream session이 바뀌거나 track이 만료되면 이전 identity_candidate와 cooldown 상태를 새 track에 전달하지 않는다.
참고: [ByteTrack 논문](https://arxiv.org/abs/2110.06864)

---

# 9. Face Detection

Person bbox 내부에서 얼굴을 찾는다.
가능하면 전체 frame에 매번 face detector를 실행하지 않는다.
Face Detection 결과:
bbox
landmarks
confidence
yaw
pitch
roll
blur_score
brightness
face_size

---

# 10. Face Quality

얼굴 embedding을 만들기 전에 quality filtering을 수행한다.
고려 요소:
face size
blur
brightness
yaw
pitch
detection confidence
occlusion 가능성
quality score:
0.0 ~ 1.0
quality가 threshold보다 낮으면 ArcFace embedding을 생성하지 않는다.
Track 중 가장 좋은 얼굴을 best face로 유지한다.

---

# 11. Face Recognition

InsightFace / ArcFace를 사용한다.
얼굴 landmark를 이용하여 alignment한다.
embedding을 생성한다.
가능하면 512-dimensional embedding을 사용한다.
embedding은 L2 normalize한다.
cosine similarity를 사용한다.

---

# 12. Target Person 등록

사용자가 찾을 사람을 등록할 수 있어야 한다.
API:
POST /api/persons
POST /api/persons/{person_id}/faces
GET /api/persons
GET /api/persons/{person_id}
DELETE /api/persons/{person_id}
Person:
person_id
name
description
enabled
created_at
PersonFace:
face_id
person_id
image_path
embedding_id
quality
created_at
한 사람에게 여러 reference face를 등록할 수 있어야 한다.
정면뿐 아니라 서로 다른 각도의 얼굴을 여러 장 등록할 수 있게 한다.
등록 사진에서 얼굴이 없거나 여러 개 검출되면 명확한 오류 또는 얼굴 선택 절차를 제공한다.
인물/얼굴 삭제 및 비활성화는 worker cache와 Qdrant 검색 대상에도 반영한다.

---

# 13. 실시간 얼굴 검색

CCTV에서 좋은 얼굴 embedding이 생성되면 등록된 target과 비교한다.
등록자가 적으면 memory cache를 사용할 수 있다.
등록자가 많아지면 Qdrant ANN search를 사용한다.
FaceSearchProvider abstraction을 만든다.
예:

```python
class FaceSearchProvider:
    def search(self, embedding, limit=10):
        ...
```

구현:
MemoryFaceSearchProvider
QdrantFaceSearchProvider

---

# 14. Face Matching

Similarity:
cosine similarity
환경 변수:
FACE_MATCH_THRESHOLD=0.75
모델과 CCTV 환경에 대한 calibration 전의 시험값으로 취급한다.
여러 reference face의 점수를 person 단위로 합산하는 규칙을 명시하고, Memory 및 Qdrant provider에서 같은 판정 규칙을 사용한다.
하지만 이 값을 절대적인 동일인 판정 기준으로 사용하지 않는다.
결과는:
candidate match
로 취급한다.
운영자가 확인할 수 있어야 한다.

---

# 15. Match Event

candidate가 발견되면 event를 생성한다.
MatchEvent:
event_id
person_id
camera_id
stream_session_id
track_id
timestamp
face_similarity
face_quality
face_image_path
frame_image_path
video_clip_path
status
status:
candidate
confirmed
rejected
동일 track에서 같은 사람이 계속 발견될 경우 event가 반복 생성되지 않도록 한다.
EVENT_COOLDOWN을 구현한다.
중복 판단 기준은 camera_id + stream_session_id + track_id + person_id다.
EVENT_COOLDOWN의 단위를 초로 정의하고, 동시 후보 및 재시도에서도 중복 event가 생성되지 않도록 처리한다.
같은 track의 점수/얼굴 개선은 기존 event 갱신으로 처리할 수 있게 한다.

---

# 16. 실시간 WebSocket

FastAPI WebSocket을 사용한다.
endpoint:
/ws/events
MatchEvent가 발생하면 Angular에 즉시 전달한다.
이 endpoint는 검색 이벤트를 전달한다. 영상 미리보기는 17절의 별도 endpoint를 사용한다.
WebSocket 연결은 인증 및 Origin을 검증하고, 느린 클라이언트의 outbound queue에는 상한을 둔다.
클라이언트는 event_id로 중복 수신을 제거한다. 재연결 시 GET /api/events?after_id=... 등으로 누락된 저장 이벤트를 복구한다.
이벤트 조회는 권한 검사와 pagination을 적용하고, event_id의 정렬/조회 규칙을 API 문서에 정의한다.
Example:

```json
{
    "type": "person_match",
    "event_id": 12345,
    "camera_id": 3,
    "camera_name": "Entrance",
    "person_id": 12,
    "person_name": "Target 01",
    "stream_session_id": "...",
    "track_id": 381,
    "timestamp": "...",
    "face_similarity": 0.87,
    "face_quality": 0.91,
    "thumbnail_url": "..."
}
```

---

# 17. Angular UI

다음 화면을 구현한다.
Dashboard
Cameras
Persons
Live Search
Events
Settings
Live Search:
왼쪽:
CCTV 목록
중앙:
Live CCTV
person bbox
track ID
candidate 표시
오른쪽:
실시간 MatchEvent
각 event:
등록된 사람 사진
검출된 얼굴
camera
timestamp
similarity
face quality
status
브라우저 영상 미리보기:

- Phase 2부터 MP4 virtual camera를 브라우저에서 볼 수 있는 미리보기 endpoint를 구현한다.
- MVP는 GET /api/cameras/{camera_id}/preview의 MJPEG로 시작한다. 기존 분석 frame에 bbox/track ID를 그린 JPEG를 제한된 FPS로 전달하여 영상과 표시가 일치하게 한다.
- 미리보기는 inference worker의 결과를 재사용한다. 시청자마다 RTSP 연결이나 GPU 추론을 새로 생성하지 않는다.
- RTSP 입력은 서버에서 처리하고, 브라우저에 카메라 RTSP URL 및 credentials를 전달하지 않는다.
- 영상 endpoint에도 인증, 카메라별 권한, 동시 시청자 수 및 전달 queue 상한을 적용한다.
- 이후 WebRTC 또는 HLS를 적용할 때는 요구 지연 시간과 encoding 비용을 측정하여 선택한다. 별도 bbox overlay를 사용할 경우 frame timestamp와 좌표 변환으로 영상과 동기화한다.

---

# 18. Candidate 확인

운영자가 검색 결과를 확인할 수 있어야 한다.
API:
POST /api/events/{event_id}/confirm
POST /api/events/{event_id}/reject
confirmed/rejected 결과는 DB에 저장한다.
향후 threshold calibration에 활용한다.

---

# 19. Event Video Clip

Match 발생 시 전후 영상을 저장한다.
기본값:
VIDEO_BUFFER_BEFORE=5
VIDEO_BUFFER_AFTER=10
각 camera에 circular video/frame buffer를 유지한다.
예:
data/events/
2026/
10/
02/
CAM03/
EVENT_12345.mp4
메모리를 과도하게 사용하지 않도록 설계한다.
클립용 pre-event buffer는 FFmpeg 기반 압축 segment를 디스크에 보관하는 방식을 우선 검토한다.
1080p, 30 FPS, 4채널의 비압축 BGR frame을 5초 저장하면 약 3.7GB의 RAM이 필요하므로, 원본 frame을 장시간 RAM에 쌓는 방식을 기본으로 사용하지 않는다.
분석용 latest-frame buffer와 클립 녹화용 buffer의 보관 정책을 구분한다.
VIDEO_BUFFER_MAX_BYTES_PER_CAMERA, STORAGE_MAX_BYTES, STORAGE_MIN_FREE_BYTES 등의 상한과 검사 주기를 설정한다.
segment 길이와 keyframe 간격을 고려하여 요청한 전후 구간을 확보하고, 겹치는 이벤트는 녹화 자원을 공유할 수 있게 한다.
clip 상태는 pending/ready/failed로 관리한다. 후속 10초 녹화와 encoding은 검색 및 WebSocket 전달을 막지 않는다.
보관 기간 만료 시 temporary segment, clip 및 관련 파일을 정리한다. 디스크 한도 도달 시 녹화 실패 상태를 보고하고 검색은 계속 수행한다.

---

# 20. Qdrant

Qdrant는 Ubuntu에서 native service로 실행한다.
Docker를 사용하지 않는다.
공식 배포 바이너리 또는 검증한 source build를 사용하며 Qdrant 버전과 설치 방법을 기록한다.
systemd service의 실행 사용자, config 및 storage 경로를 명시하고, localhost에 바인딩한다.
Phase 1에서 실제 서버 연결과 시험용 collection의 생성, upsert, query, 삭제를 검증한다. qdrant-client 설치만으로 service 연결 검증을 통과 처리하지 않는다.
collection:
face_embeddings
payload:
person_id
face_id
quality
model_version
created_at
vector dimension과 distance metric은 선택한 얼굴 모델에 맞춰 고정한다. 서로 다른 model version의 embedding을 같은 검색 공간에 섞지 않는다.
향후 CCTV에서 발견된 얼굴도 별도 collection에 저장할 수 있게 한다.

---

# 21. MariaDB

MariaDB는 Ubuntu native package/service를 사용한다.
SQLAlchemy 2.x
Alembic
tables:
users
cameras
persons
person_faces
tracks
face_detections
match_events
camera_connections
audit_logs
system_settings
적절한:
PRIMARY KEY
FOREIGN KEY
INDEX
UNIQUE constraint
를 설정한다.
위 table 목록은 전체 개발 목표다. 각 Phase에서 사용하는 table만 migration으로 추가한다.
Phase 1에서는 인증 및 카메라 기본 정보 등 기반 기능에 필요한 최소 schema와 Alembic upgrade를 검증한다.
MariaDB, Qdrant, 파일 저장소 사이의 작업은 단일 DB transaction으로 원자성을 보장한다고 가정하지 않는다.
인물/얼굴 삭제는 재시도 가능한 정리 상태를 기록하여 DB record, Qdrant vector, 이미지 및 worker cache를 함께 정리한다.
정리가 진행 중인 인물은 즉시 검색 대상에서 제외하고, 실패한 정리 작업은 재시도하여 잔여 데이터를 처리한다.

---

# 22. Person Re-ID

Person Re-ID는 별도 interface로 만든다.

```python
class PersonReIdentifier:
    def extract_embedding(self, person_crop):
        ...
    def compare(self, embedding_a, embedding_b):
        ...
```

초기 MVP에서는 disabled 가능하다.
향후:
Face similarity
Person Re-ID similarity
Tracking continuity
Camera topology
Travel time
를 결합할 수 있게 한다.

---

# 23. Camera Topology

여러 CCTV 사이의 이동 관계를 정의한다.
camera_connections:
from_camera_id
to_camera_id
min_travel_seconds
max_travel_seconds
distance
향후:
CAM01
↓
CAM03
↓
CAM07
같은 이동경로 분석에 사용한다.
물리적으로 불가능한 이동 후보는 낮은 점수 또는 제외할 수 있도록 한다.

---

# 24. RTX 3070 Ti 성능 최적화

Target GPU:
RTX 3070 Ti 8GB
초기 목표:
1080p CCTV 4 channels
AI processing:
5~10 FPS per camera를 목표로 한다.
단, 고정적으로 이 성능을 보장한다고 가정하지 말고 benchmark를 통해 실제 처리량을 측정한다.
모니터링:
GPU utilization
GPU memory
source FPS
processed FPS
detection FPS
face detection FPS
ArcFace inference rate
queue size
dropped frames
average latency
P95 latency
Phase 2부터 1채널 MP4 benchmark를 실행하고, Phase 3에서 face analysis를 포함하여 다시 측정한다.
1채널에서 RAM/VRAM 사용량과 지연 시간을 확인한 뒤 2채널, 4채널로 확장한다.
입력 해상도, detector resize, 모델 버전, GPU 실행 장치, 사람 수와 얼굴 분석 요청량을 benchmark 조건으로 기록한다.

---

# 25. Benchmark

scripts/benchmark.py
입력:
MP4
또는
RTSP
출력:
resolution
source FPS
processed FPS
detection FPS
face detection FPS
face recognition/sec
GPU memory
GPU utilization
average latency
P95 latency
dropped frames
목적:
RTX 3070 Ti에서 실제 몇 개 CCTV까지 처리 가능한지 측정한다.
실시간 재생 속도로 입력하는 모드와 가능한 최대 속도로 처리하는 offline 모드를 구분한다.
실시간 모드의 latency는 frame capture 시각부터 분석 결과가 준비될 때까지의 시간으로 정의하고, inference 시간은 별도 측정한다.
각 camera별 지표와 전체 합산 지표를 기록한다. Phase 9는 초기 benchmark의 확장 및 최적화 단계다.

---

# 26. Face Recognition Calibration

매우 중요하다.
scripts/calibrate_face_threshold.py
confirmed/rejected event 데이터를 사용하여 threshold를 평가할 수 있게 한다.
이벤트 검토 결과는 운영 중 피드백으로 활용하며, 별도 정답 데이터로 threshold의 성능을 평가한다.
이미 생성된 candidate event만으로는 임계값 아래에서 놓친 정답과 비후보 사례를 알 수 없으므로 Recall, FPR, FNR 및 ROC를 정확하게 평가할 수 없다.
평가 데이터:

- 정답 인물과 출현 시간/구간이 표시된 시험용 MP4 및 얼굴 sample을 사용한다.
- 등록 인물의 양성 사례, 서로 다른 인물 및 미등록 인물의 음성 사례, 임계값 아래 score의 사례를 포함한다.
- 동일 영상의 인접 frame이 calibration set과 evaluation set 양쪽에 섞이지 않도록 영상/촬영 session 단위로 분리한다.
- 평가에 포함할 모델 버전, gallery 구성, reference face 합산 규칙, 얼굴 quality 및 frame sampling 설정을 기록한다.
- 얼굴 유사도 비교 성능과 detection/quality filtering을 포함한 전체 검색 파이프라인 성능을 구분한다.
- 판정 단위가 얼굴 쌍인지 track/출현 구간인지 명시하고, ground truth가 부족한 지표는 unavailable로 보고한다.

정답 데이터가 확보된 범위에서 다음 지표를 계산한다.
True Positive
False Positive
False Negative
True Negative
Precision
Recall
FPR
FNR
ROC
1:N 검색에 대해서는 미등록 인물 검색의 오탐률과 등록 인물 검색의 누락률도 별도로 측정한다.
threshold별 결과를 출력한다.
예:
Threshold   Precision   Recall   FPR
0.65
0.70
0.75
0.80
0.85
FACE_MATCH_THRESHOLD를 임의의 인터넷 값에 의존하지 않고 실제 CCTV 환경에서 결정할 수 있게 한다.

---

# 27. 보안

얼굴 데이터는 민감한 생체정보라고 가정한다.
구현:
authentication
role-based authorization
audit logging
data retention
face image access control
API authorization
원본 얼굴 사진을 public static directory에 노출하지 않는다.
embedding을 API에서 그대로 반환하지 않는다.
RTSP password를 로그에 남기지 않는다.
검색 결과를 자동적으로 동일인이라고 확정하지 않는다.
candidate → confirmed/rejected
workflow를 사용한다.
개발 단계:

- Phase 1에서 로그인, 최소 role 기반 권한 검사 및 환경 변수 기반 secret 관리의 기반을 구현한다. 계정 secret을 기본값으로 하드코딩하지 않는다.
- Phase 2의 영상 미리보기에 접근 제어를 적용한다.
- Phase 4에서 인물/얼굴 등록을 제공하기 전에 해당 API 및 이미지 조회의 인증/권한 검사, 감사 기록, 삭제·보관 정책을 적용한다.
- Phase 6의 이벤트 조회, 확인/거부 및 WebSocket에도 같은 권한 규칙을 적용한다.
- 비상업 시험 환경에서도 얼굴 사진, embedding, RTSP 및 DB credentials를 보호한다.

저장소 관리:

- 첫 commit 전에 .gitignore에 DBCONFIG.md, .env, .venv/, node_modules/, data/, logs/, 다운로드한 모델 가중치 및 생성 파일을 제외한다.
- .env.example에는 이름과 설명 및 안전한 예시만 작성하고, 실제 DB credentials를 복사하지 않는다.
- 보관 기간은 이미지, embedding, event 및 clip 종류별로 설정하고, 실제 cleanup 작업을 구현한다.

---

# 28. Logging

logs/
application.log
ai.log
camera.log
event.log
로그 rotation을 구현한다.
민감정보는 기록하지 않는다.

---

# 29. systemd

Docker 대신 production 실행은 systemd를 사용한다.
예:
cctv-backend.service
cctv-worker.service
필요하면:
cctv-frontend.service
또는 Angular production build를 Nginx에서 제공한다.
systemd service 파일 예제를:
deploy/systemd/
에 작성한다.
API와 worker는 별도의 service로 실행하고, 5절의 내부 통신 및 단일 GPU worker 조건을 유지한다.
Qdrant native service도 deploy/systemd/에 예제를 작성한다.
Restart 정책, 실행 사용자, 환경 변수 파일, working directory 및 storage 쓰기 권한을 명시한다.

---

# 30. Nginx

production에서는 Angular와 FastAPI 앞에 Nginx를 사용할 수 있게 한다.
구조:
Browser
↓
Nginx
├── Angular static files
├── /api → FastAPI
└── /ws → FastAPI WebSocket
MVP의 /api/cameras/{camera_id}/preview에는 MJPEG 전달에 맞는 proxy buffering 및 timeout 설정을 적용한다.
Nginx example configuration을:
deploy/nginx/
에 작성한다.

---

# 31. 테스트

unit test를 작성한다.
최소:
GPU detection test
face embedding test
cosine similarity test
face quality test
event deduplication test
camera reconnect test
database CRUD test
WebSocket event test
stream session 변경 후 track identity/cooldown 초기화 test
동시 후보 및 재시도에 대한 event deduplication test
이미지/영상 및 인물 API의 인증/권한 test
인물 삭제의 vector/file/cache 정리 및 실패 재시도 test
실제 CCTV가 없어도 MP4 파일을 virtual camera처럼 사용할 수 있게 한다.
GPU 및 실제 모델 검증은 integration test로 구분한다. GPU가 없는 환경에서 skip한 결과를 GPU 검증 성공으로 보고하지 않는다.
DB test는 시험용 database를 사용하고 기존 database의 데이터를 삭제하거나 초기화하지 않는다.

---

# 32. 개발 단계

모든 기능을 한 번에 구현하지 않는다.
각 Phase마다 실제 실행 가능한 상태를 유지한다.
Phase 1
프로젝트 skeleton
Python venv
FastAPI
MariaDB
Qdrant
Angular
GPU 확인
로그인 및 최소 권한 검사 기반
secret 관리와 .gitignore
API/worker 프로세스 구성 및 내부 통신 설계 문서
완료 기준:

- Python venv에서 backend가 실행되고 health endpoint가 응답한다.
- MariaDB의 시험용 schema에 Alembic upgrade가 적용되고 기본 CRUD가 동작한다.
- Qdrant native service에 실제 연결하여 시험용 collection의 생성/upsert/query/삭제 검증이 통과한다.
- Angular 개발 서버에서 backend health 상태를 확인할 수 있고 production build가 성공한다.
- 로그인과 최소 권한 검사가 동작하고 secret이 코드/로그에 노출되지 않는다.
- scripts/check_gpu.py로 PyTorch CUDA 연산 및 작은 ONNX model의 CUDA 추론 결과를 보고한다. CPU fallback 또는 미검증 항목은 별도로 표시한다.
- dependency 버전 조합과 실제 설치/실행 명령이 문서에 기록된다.
- 실제 사용한 구현/모델의 라이선스 정보가 기록된다. 후속 Phase의 모델 선정은 계획으로 표시한다.

Phase 1에는 CCTV inference, 얼굴 등록, MatchEvent 및 전체 Live Search 기능을 포함하지 않는다.
Phase 2
MP4 입력
YOLO person detection
tracking
화면에 bbox/track ID 표시
인증된 MP4 MJPEG 미리보기 endpoint 및 간단한 Angular 표시
단일 GPU worker 및 camera 명령/상태 내부 통신 구현
선택한 YOLO 모델의 실제 CUDA 추론 검증
1채널 MP4 초기 benchmark
Phase 3
face detection
face alignment
face quality
ArcFace embedding
선택한 얼굴 모델의 실제 CUDA 추론 검증
얼굴 분석을 포함한 1채널 benchmark와 resource 사용량 측정
calibration용 정답 데이터 형식 정의 및 시험 데이터 준비
Phase 4
target person 등록
여러 reference face
face similarity search
인물/이미지 접근 제어 및 감사 기록
인물/얼굴 삭제의 DB/vector/file/cache 정리와 retention cleanup
Phase 5
RTSP 실시간 입력
reconnect
frame dropping
stream session lifecycle 및 track/cooldown 초기화
Phase 6
MatchEvent
WebSocket
event 중복 방지, 인증된 이벤트 조회 및 재연결 복구
Phase 7
Angular Live Search UI
Phase 8
event clip recording
Phase 9
다중 camera benchmark
GPU optimization
Phase 10
face threshold calibration
Phase 11
Person Re-ID
Phase 12
multi-camera tracking
camera topology

---

# 33. 각 Phase 작업 규칙

각 Phase를 완료하면 반드시:

1. 구현 내용을 요약한다.
2. 변경된 파일을 보여준다.
3. 설치 또는 실행 명령을 알려준다.
4. 테스트를 실행한다.
5. 실패한 테스트가 있으면 원인을 분석하고 수정한다.
6. GPU 관련 코드라면 실제 CUDA 사용 여부를 확인한다.
7. 다음 Phase로 넘어가기 전에 현재 상태가 실행 가능한지 확인한다.
8. git commit하기 좋은 단위로 변경사항을 유지한다.
9. 모델/라이브러리 버전과 사용 조건 변경을 문서에 반영한다.
10. 미구현, skip, CPU fallback 및 외부 환경으로 검증하지 못한 항목을 성공 결과와 구분해서 보고한다.

Phase 1은 위 완료 기준의 실제 검증 결과를 기준으로 보고하고, 실패 또는 미검증 조건이 있으면 이를 명시한다.
기존 파일이 있다면 무조건 덮어쓰지 않는다.
먼저 내용을 읽고 필요한 부분만 수정한다.

---

# 34. 첫 번째 작업

현재 directory를 먼저 조사한다.
기존 파일이 있다면:
project structure
Python version
Node version
CUDA
NVIDIA driver
MariaDB
Qdrant
FFmpeg
를 확인한다.
다음 명령에 해당하는 환경을 확인한다.
nvidia-smi
python3 --version
node --version
npm --version
ffmpeg -version
mariadb --version
GPU와 CUDA compatibility를 확인한다.
그 다음:
docs/architecture.md
docs/models.md
README.md
.env.example
.gitignore
requirements.txt
requirements.lock
프로젝트 skeleton
을 만든다.
architecture 문서에는 GPU model 소유 프로세스, camera별 상태, API/worker 내부 통신, 영상 미리보기 방식, event 중복 기준 및 data cleanup 규칙을 명시한다.
README에는:
Architecture
Ubuntu installation
Python venv setup
CUDA/GPU setup
MariaDB setup
Qdrant setup
Angular setup
Running backend
Running frontend
Running with MP4
Running with RTSP
API
Benchmark
systemd deployment
Nginx deployment
Troubleshooting
을 작성한다.
현재 구현한 기능의 실행 명령은 실제로 검증하고, MP4/RTSP/Benchmark 등 후속 Phase의 기능은 구현 예정으로 명시한다.
아직 실행되지 않는 명령과 API를 이미 동작하는 기능처럼 설명하지 않는다.
그 후 Phase 1을 구현한다.
중요:
처음부터 모든 Phase를 구현하지 마라.
우선 Phase 1까지만 구현하고 테스트한 후 현재 상태와 다음 단계에서 수행할 작업을 보고해라.
DBCONFIG.md에서 DB 연결 정보 참고해. 해당 내용은 로그, README, .env.example 또는 commit에 노출하지 않는다.
연결 정보가 가리키는 기존 database와 시험용 schema를 확인하고, migration 및 test가 기존 데이터를 초기화하지 않도록 한다.
