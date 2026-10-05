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
best face는 대표 썸네일용으로 유지하고, 비교에는 최근 품질 통과 사진을 기본 최대 5장 사용한다. 이후 사진은 기존 best보다 품질이 낮아도 비교 대상에 포함한다.

---

# 11. Face Recognition

InsightFace / ArcFace를 사용한다.
얼굴 landmark를 이용하여 alignment한다.
embedding을 생성한다.
가능하면 512-dimensional embedding을 사용한다.
embedding은 L2 normalize한다.
cosine similarity를 사용한다.
영상의 같은 추적에서는 사진별 인물 점수를 품질에 따라 평균하고, 평균과 최소 2장의 개별 점수가 모두 기준을 충족할 때 후보로 표시한다. 영상 세션·추적 만료와 큰 특징 변화에서 이전 사진을 비운다.

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


## 구현 진행 기록 (2026-10-02)

Phase 4까지 구현 및 native 서비스 반영 완료. 인물/다중 reference 등록, Memory/Qdrant cosine 검색과 인물별 최대 점수, 인증된 이미지·인물 grant·감사 기록, worker cache ACK 및 durable 삭제 재시도, 이미지/특징별 retention cleanup을 제공한다. 단위 테스트 45개, 실제 DB 통합 1개, Chrome E2E 4개와 CUDA 기능 검증을 통과했다. 공개 smoke 자료의 동작 확인이며 정확도 보정/동일인 확정으로 해석하지 않는다. 자세한 내용은 [Phase 4 검증 결과](docs/phase4-report.md)를 참고한다.

Phase 5까지 구현 및 native 서비스 반영 완료. RTSP 연결/읽기 실패 시 제한된 지수 backoff로 자동 재연결하고, 이전 영상·추적·얼굴·검색 후보를 제거한 새 stream session에서 복구한다. 최신 대기 frame 1개를 유지하며 늦게 끝난 이전 session의 추론 결과와 예외를 차단한다. 재시도 중 중지, 인증된 미리보기의 자동 복구 및 재연결 통계를 제공한다. 단위 테스트 54개, 실제 DB 통합 1개, Chrome E2E 5개 및 공개 localhost RTSP를 통한 실제 CUDA 중단/복구 검증을 통과했다. 신규 AI 모델/상용 서비스는 추가하지 않았고 비상업 시험 목적을 유지한다. 자세한 구현·실행 명령·측정 한계는 [Phase 5 검증 결과](docs/phase5-report.md)를 참고한다.

Phase 5 완료 시 다음 범위는 Phase 6의 MatchEvent 저장, 인증된 WebSocket, 이벤트 중복 방지 및 DB 조회 재연결 복구로 정했다. 아래 2026-10-03 Phase 6 기록에 실제 진행 상태를 구분한다.

2026-10-03 인물 관리 화면 변경: 인물 추가·수정·조회는 전용 페이지에서 진행한다. 목록은 첫 화면에 유지하고 등록/수정 페이지에서 얼굴 사진 등록·삭제와 사진 시험 비교를 제공한다. 저장 후 수정 URL로 전환하며 새로고침·브라우저 뒤로/앞으로 가기에서 정보를 복원한다. Angular build/TypeScript 및 전체 Chrome E2E 5개를 통과했다.

2026-10-03 얼굴 사진 크롭: 로컬 JPEG/PNG 선택 후 영역 지정·이동·크기 조절·미리보기와 명시적인 얼굴 저장을 제공한다. 인물 생성 전에도 사진을 선택하고 크롭한 얼굴 저장 시 인물을 생성해 연결한다. 사진 저장 실패 시 생성된 인물과 크롭을 유지한다. 좌표/저장 흐름 테스트 5개와 타입 검사·빌드를 통과했다. Chrome E2E는 최초 실행에서 브라우저 제한으로 미검증이었으며 아래 권한 변경 후 검증에서 통과했다.

2026-10-03 Phase 6 코드 및 격리 검증 완료: session별 tracks, MatchEvent와 commit 순서의 change journal, 별도 bounded DB/file 저장 thread, 기본 30초 cooldown과 UNIQUE 중복 방지, 인증된 조회/JPEG/WebSocket, CSRF와 운영 grant를 확인하는 확인·거부 및 감사 기록을 구현했다. after_id는 신규 이벤트를, after_change_id는 개선/검토 변경까지 복구한다. 영상 분석 화면에 최근 100건을 표시하며 event_id/change_id로 중복·늦은 응답을 처리한다. 사진 7일/기록 30일, 500 MB/50,000건 상한과 실제 cleanup을 추가했다. 신규 backend 27건과 기존 독립 4건, frontend 12건, Ruff·타입 검사·production build·SQLite migration/모델 일치 및 MariaDB offline DDL을 통과했다.

Phase 6 **서비스 반영과 실제 CUDA 파이프라인 검증 완료**: 에이전트 실행 환경에서는 MariaDB/localhost 접근이 차단되어 사용자 terminal에서 실행했다. 서비스 재시작은 status=passed, resumed_cameras=[]다. native smoke의 정상 YOLO 장치 cuda:0 검사 오류를 수정하고 HTTP/WS 모의 회귀 10건을 추가하여 backend 격리 테스트 합계 41건을 통과했다. 이후 사용자 terminal에서 check_phase6.py를 재실행하여 2026-10-03 08:23:40 KST에 status=passed와 실제 검증 8개 항목을 확인했다. detector_device=cuda:0, face_device=cuda이며 후보 생성→DB/JPEG→인증된 WebSocket, 중복 방지, 확인/거부 저장·갱신, 오프라인 변경 복구와 새 session 이벤트를 통과했다. purpose=pipeline_smoke, accuracy_calibrated=false다. 당시 전체 backend 회귀·실제 DB 통합 테스트·Chrome E2E는 미검증이었으며 아래 권한 변경 후 검증에서 완료했다. 실제 Alembic head의 직접 조회는 DB 통합 테스트에 포함되어 있다. 적용 순서와 검증 범위는 [Phase 6 결과](docs/phase6-report.md)에 기록했다. 모델/가중치/라이선스·의존성 변경 없이 비상업 시험 목적을 유지한다.

2026-10-03 전체 회귀·브라우저 검증 시도: backend 91건을 수집했으나 첫 fixture의 TestClient/AnyIO portal 기동에서 45초 timeout이 발생했고 asyncio wakeup socket 전송의 PermissionError(errno=1)를 확인했다. 실제 DB 통합 1건은 DB 연결 제한으로 실패했다. 기존 Chrome E2E 6건과 신규 Phase 6 1건 모두 Chrome 기동의 setsockopt EPERM/SIGTRAP로 차단되어 당시 실제 화면 검증은 미완료였다. 이벤트 화면의 확인·거부, WebSocket 중단 후 HTTP cursor 복구, 새로고침·모바일 표시·로그아웃을 검사하는 phase6.spec.ts를 추가했고 Playwright 7건 수집을 통과했다. 실행 가능한 backend 격리 41건/frontend 12건, 애플리케이션 타입 검사·production build·Ruff를 재검증했다. 기존 native smoke 통과 결과는 유지했으며 이 제한 환경의 시도는 data/reports/phase6-regression-initial.json에 status=partial로 보존했다.

2026-10-03 권한 변경 후 **Phase 6 전체 회귀·브라우저 검증 완료**: 에이전트에서 DB/localhost/asyncio/Chrome 접근이 정상임을 확인했다. 전체 backend 91건과 실제 MariaDB 통합 1건을 통과했으며 실제 migration head는 `0005_match_events`다. Chrome 첫 실행에서 얼굴 Blob 전송 데이터 읽기와 WebSocket 한쪽만 닫는 테스트 코드 문제를 수정한 뒤 전체 E2E 7건을 통과했다. 실제 얼굴 크롭 JPEG 180×240 전송·112×112 등록 사진, 신규/수정/취소/재등록, RTSP 중단/복구, 이벤트 확인·거부, WebSocket 중단 중 저장한 검토의 HTTP cursor 복구·새로고침·모바일 표시·로그아웃을 확인했다. frontend 논리 테스트 12건·애플리케이션 타입 검사·production build(529.81 kB)·Ruff도 통과했다. 기존 카메라 2개·인물 3명·얼굴 2개·계정 1개와 카메라 실행 상태를 보존했고 임시 자료를 정리했다. 최종 결과는 `data/reports/phase6-regression.json`, 자세한 범위는 [Phase 6 결과](docs/phase6-report.md)에 기록했다. 이전 접근 제한 기록은 과거 시도의 결과다.

Phase 6 검증 후 다음 개발 범위는 Phase 7 전체 Live Search UI로 정했다. clip recording과 다중 camera 최적화는 각각 Phase 8/9 범위다.

2026-10-03 Phase 7 Angular Live Search UI: 왼쪽 조회 가능한 CCTV 목록/이름·위치 검색, 중앙 인증된 영상/person bbox/track ID/추적별 후보 강조·후보 수 및 분석 조작, 오른쪽 실시간 MatchEvent/현재 등록 얼굴·검출 얼굴 비교와 확인·거부를 제공한다. 카메라 범위/인물 이름/상태 필터는 최근 100건의 표시만 바꾸며 WebSocket/HTTP 변경 cursor 복구를 유지한다. 큰 화면의 3열과 390 px 모바일, `#/live/{camera_id}`의 직접 진입·새로고침·뒤로 가기 복원, 만료·삭제 사진 안내와 로그인 이후 늦은 응답 차단을 구현했다. 현재 계정의 can_view/can_operate를 카메라 API에 추가하여 목록·조작 버튼을 표시하고 기존 서버 grant/CSRF 검사를 유지한다. backend만 재시작하여 Phase 7 서비스 반영을 확인했고 worker/GPU/모델/DB migration/의존성은 변경하지 않았다. 인물 관리 메뉴의 마지막 위치와 기존 크롭/등록 흐름을 유지한다. backend 96건, 실제 DB 통합 1건과 frontend 논리 16건·타입 검사·Angular production build·Ruff를 통과했다. 전체 Chrome 8건을 실패/건너뜀/재시도 없이 통과하여 실제 CUDA 후보·두 사진·필터·카메라 전환·URL 복원·모바일·로그아웃을 검증했다. 시험 전후 기존 카메라 2개·인물 3명·얼굴 2개·계정 1개의 ID와 카메라 실행 상태가 일치했고 임시 자료를 정리했다. 최종 결과는 data/reports/phase7-regression.json에 status=passed로 저장했으며 자세한 결과는 [Phase 7 결과](docs/phase7-report.md)에 기록했다.

다음 개발 범위는 Phase 8 이벤트 전후 영상 클립이다. 비상업 시험 목적과 기존 모델 이용 조건을 유지한다.

2026-10-03 사용자 요청에 따른 얼굴 분석 개선: 왼쪽 [로그]·[기능 설정] 메뉴와 전용 페이지를 추가하고 인물 관리는 마지막에 유지한다. 인물별 검사 시각·카메라/세션/추적/프레임, 제외 사유, 품질과 비교 결과를 MariaDB에 기록하며 필터·주요 사유 집계·cursor 조회를 제공한다. 사진·특징·인물 이름·RTSP 주소는 로그에 저장하지 않으며 기본 7일/100,000건 상한을 적용한다. 관리자는 검출 FPS·얼굴 검사 간격·프레임당 검사 인원을 DB에 저장하고 실행 중인 분석에 반영한다. worker 재시작 복구, 미반영 상태·자동 재시도와 수정 충돌 검사를 제공한다. 머리 영역 우선 SCRFD/전체 영역 fallback/640px 재검사와 최근 최대 5장·품질 가중 평균·최소 2장 기준을 구현했다. 이벤트 사진은 해당 후보를 지지한 프레임에서 선택하고 기존 중복 방지·인증·재연결을 유지한다. 실제 migration head는 `0006_recognition_controls`다. backend 110건+MariaDB 통합 1건, 전체 Chrome 9건, frontend 논리 16건·타입/production build·Ruff를 통과했다. 상세 내용은 [얼굴 분석 개선 결과](docs/recognition-controls-report.md)를 참고한다. 움직이는 인물의 독립 정확도 보정은 후속 평가이며 Phase 8 클립 구현은 아직 진행하지 않았다.

2026-10-03 Live Search 검색 이벤트 삭제: 개별 삭제와 현재 필터에 표시된 삭제 가능 목록(최대 100건)의 삭제를 추가했다. 삭제 확인·취소, 기존 운영/인물 grant·CSRF 검사와 감사 기록, 검출 얼굴·프레임 파일 삭제를 제공한다. 같은 세션·추적·인물의 중복 방지 키를 유지하며, 삭제 journal을 인증된 WebSocket/HTTP cursor로 전달해 다른 화면·새로고침·오프라인 재연결에서도 삭제를 복구한다. 전체 backend 112건+MariaDB 통합 1건, frontend 논리 19건·타입/production build·Ruff 및 관련 Chrome 3건을 통과했다. backend 서비스에 반영했으며 worker/기능 설정/기존 카메라는 변경하지 않았다. 추가 migration 없이 `0006_recognition_controls`를 유지한다. 상세 내용은 [이벤트 삭제 결과](docs/event-deletion-report.md)를 참고한다.

2026-10-03 비교점수 기준 설정: 기능 설정에 `face_match_threshold`(-1~1, 기본 0.75)를 추가하고 DB 저장·실행 중 반영·현재 적용값 표시를 제공한다. 기존 설정 JSON은 환경 기준을 병합하여 이전 값을 유지한다. Live Search의 보관 사진 점수 재판정, 신규 이벤트 저장과 인물 관리 사진 시험 비교에 같은 기준을 사용하며 기존 이벤트/세션/추적은 유지한다. 이벤트 저장은 현재 DB 기준을 다시 검사하여 설정 변경 이전의 대기 후보를 처리한다. 전체 backend 116건+MariaDB 통합 1건, frontend 논리 19건·타입/production build·Ruff와 관련 Chrome 3건을 통과했다. 서비스 반영과 기존 설정/데이터 보존을 확인했으며 추가 migration은 없다. 자세한 내용은 [비교점수 설정 결과](docs/match-threshold-report.md)를 참고한다.

2026-10-03 Phase 8 구현·서비스 반영: 카메라/세션별 압축 디스크 세그먼트, 독립된 녹화 대기열·MP4 인코딩, pending/ready/failed·일부 구간 표시, 저장 상한·여유 공간·보관 정리 및 인증된 Range 재생을 추가했다. DB head 0007_event_clips, backend 119건+실제 MariaDB 1건, frontend 19건·타입/build·Ruff와 실제 CUDA 후보의 Chrome 클립 재생 검증을 통과했다. 상세 변경·명령·결과는 [Phase 8 결과](docs/phase8-report.md)에 기록한다.

2026-10-03 Phase 9 구현·서비스 반영: 실제 API의 1→2→4 채널 측정과 별도의 offline 처리량, 단계별 FPS·drop·queue·평균/P95·GPU/RAM 조건을 기록한다. 검출 batch·순환 scheduler·얼굴 비용에 따른 batch 축소·CPU decoder/OpenCV thread 상한과 프레임 내 gallery snapshot 재사용을 추가했다. FP16은 실제 CUDA에서 검증했으나 속도 이득이 없어 FP32를 기본 유지했다. 1080p/30 FPS 정지 사진 반복 영상과 현재 얼굴 간격 0.2초에서 4채널 목표 5~10 FPS에는 미달함을 별도 보고했다. backend 120건과 실제 MariaDB 1건을 통과했으며 상세 수치·실행 명령·검증은 [Phase 9 결과](docs/phase9-report.md)에 기록한다. DB head는 0007_event_clips이며 사용자 설정·기존 카메라를 보존했다.

2026-10-03 Phase 10 평가 도구 구현: 독립 정답 schema·hash/원본 검증, 얼굴 쌍과 품질 제외·출현 구간별 지표, ROC/AUC, 1:N 미등록 오탐·등록 누락과 calibration에서 선택한 고정 threshold의 evaluation을 제공한다. confirmed/rejected는 후보 피드백으로만 요약한다. 실제 CUDA 공개 smoke 경로를 검증했으나 독립 촬영 정답이 없어 정확도 보정·기준 제안은 unavailable이고 현재 기준 0.7을 유지했다. 설치·실행·검증·변경 목록은 [Phase 10 결과](docs/phase10-report.md)를 참고한다. 신규 모델/의존성/migration은 없다.

2026-10-04 Phase 11 구현·서비스 반영: PersonReIdentifier 인터페이스/disabled 구현과 작성자 OSNet x0.25 MSMT17 어댑터, 고정 source·weights SHA/MIT 고지, weights_only 로딩, 실제 CUDA body 특징·cosine 비교를 추가했다. 선택 활성화 시 카메라/session별 bounded RAM cache·ROI/간격 상한·종료 정리·실패 격리와 metadata-only API·Live Search 상태를 제공한다. 실제 CUDA 독립 worker와 서비스의 Chrome 활성 상태를 검증하고 runtime drop-in을 제거해 기본 disabled 및 기존 기능 설정을 복원했다. 얼굴 identity를 할당하지 않으며 카메라 간 연관/topology/travel time은 Phase 12 범위다. 설치·실행·변경·실제 검증 범위는 [Phase 11 결과](docs/phase11-report.md)에 기록한다. DB head는 0007_event_clips를 유지한다.

Phase 8→9→10→11을 요청 순서로 구현·실행 검증했으며 Phase마다 별도 git commit을 남긴다. Phase 11 최종 검증은 backend 138건+MariaDB 1건, frontend 19건·타입/build·Ruff, Chrome 활성 1건+기본 비활성/Live Search/클립 3건 및 실제 CUDA native Re-ID를 통과했다. 기본 Re-ID 비활성과 기존 기능 설정을 복원했고 API phase=11, DB head=0007_event_clips를 확인했다. 4채널 목표 FPS 미달·독립 자료 부족에 따른 실제 보정 미확정·Phase 12 이동경로 범위는 각 결과 문서에 구분했다.


2026-10-04 사용자 요청에 따른 다크 테마·얼굴 검출 테스트 구현 및 서비스 반영: 로그인과 전체 workspace 화면을 공통 다크 테마로 변경하고 첫 단계 커밋을 남겼다. 왼쪽 메뉴 마지막에 [얼굴 검출 테스트]를 추가했다. 영상 파일 선택 후 [영상 분석]을 누르면 비동기 업로드와 모든 프레임의 전체 화면/겹치는 영역 SCRFD 검사를 수행한다. ArcFace cosine 특징으로 영상 내 인물 묶음을 만들고 가장 선명한 대표 JPEG 한 장과 반복 횟수·등장 시각을 격자로 표시한다. 진행률·새로고침 복구·이전 결과 선택·실행 중 전체 삭제를 제공한다. 계정별 소유권·인증 이미지·CSRF·0700/0600 저장, 기본 24시간 보관·원본 즉시 정리, RAM-only 특징과 공유 GPU 처리·입력/저장 상한·재시작 실패 처리를 적용했다. 실제 CUDA의 공개 40프레임 영상에서 전 프레임·80회 검출·2인물 묶음(각 40회)을 확인했다. 전체 backend 151건+MariaDB 1건, frontend 19건·타입/build(610.66 kB)·Ruff, Chrome 다크 테마/실제 얼굴 추출/기존 크롭/Live Search 4건을 통과했다. 시험 계정·자료는 정리했으며 기존 인물·카메라·기능 설정과 DB head=0007_event_clips를 유지한다. 자동 묶기의 오류 가능성과 검증 자료의 한계는 [다크 테마·얼굴 검출 테스트 결과](docs/face-test-report.md)에 기록했다. 새로운 Phase 12 구현이나 모델/의존성 변경은 포함하지 않는다.

2026-10-04 얼굴 검출 테스트 설정 추가·서비스 반영: 파일 업로드 위에 [검출 기준](0.10~0.99)과 [최소 얼굴 크기](8~512 px 정수)를 추가했다. 최소 크기는 원본 얼굴 영역의 짧은 변이며 기본값은 기존 검출 기준 0.5/최소 8 px를 유지한다. 분석 시작 시 영상별 값을 저장·표시하고 새로고침 시 최근 영상의 값을 복구한다. 공유 모델 기본값을 바꾸지 않고 각 프레임 호출에 기준을 전달하며 크기 필터를 적용한다. backend 162건, frontend 19건·타입/build(613.79 kB)·Ruff, 실제 CUDA/Chrome 검증을 통과했다. 공개 40프레임 반복 영상에서 0.50/32 px는 80회·2묶음, 0.80/32 px는 40회·1묶음, 0.80/512 px는 0회로 설정의 실제 적용을 확인했다. 입력 범위·영상별 값 보존·복구·모바일 화면·인증·삭제를 확인하고 임시 시험 계정만 정리했다. 움직이는 영상의 정확도 보정은 포함하지 않으며 상세 결과는 [얼굴 검출 테스트 결과](docs/face-test-report.md)에 기록했다.

2026-10-04 얼굴 묶음 재병합·비교점수 입력 구현 및 서비스 반영: 파일 업로드 위에 [비교점수 기준](-1~1)을 추가하고 해당 영상의 프레임별 묶기와 완료 후 재병합에 함께 사용한다. 모든 프레임을 처리한 뒤 최종 평균 특징을 비교해 기준 이상인 묶음을 합친다. 모든 원래 묶음 쌍이 기준을 통과하도록 complete linkage를 사용하고 동시 등장 제외 조건을 전파해 같은 프레임에 함께 나온 얼굴과 유사도 연결에 따른 과도한 병합을 막는다. 검출 횟수·등장 시각과 최선 대표 사진을 통합하고 중복 JPEG를 정리한다. 병합 진행 상태·완료 요약·설정 복구·병합 중 삭제/재시작을 지원한다. backend 174건, frontend 19건·타입/build(616.00 kB)·Ruff, 실제 CUDA/Chrome 검증을 통과했다. 제어한 특징으로 실제 묶음 2→1 병합을 확인했고 공개 반복 영상은 비교 기준 0.80/-1.00 모두 동시 등장한 두 인물을 2개로 유지했다. 기존 사용자 결과와 공유 기능 설정을 보존했다. 기존 결과는 특징을 보관하지 않아 새 기능 적용 시 원본 영상을 다시 업로드해 분석한다. 움직이는 영상의 정확도 보정·모델/의존성/migration 변경은 없으며 상세 내용은 [얼굴 검출 테스트 결과](docs/face-test-report.md)에 기록했다.

2026-10-04 사람 검출 기준 점수 설정·Live Search 반영: 기능 설정에 [사람 검출 기준 점수](0~1, 기본 0.10)를 추가하고 기존 function_settings JSON에 저장한다. 누락된 이전 설정은 DETECTION_CONFIDENCE로 보완하며 다른 사용자 값을 보존한다. GPU scheduler가 다음 검출부터 실제 YOLO conf 인자와 상태를 갱신하고 worker 시작 시에도 저장값을 모델에 맞춘다. 카메라 세션·추적 객체·추적기의 low/high/new 기준(기본 0.10/0.50/0.60)은 유지한다. Live Search 상세 분석 지표에 해당 프레임의 적용값을 표시한다. backend 176건, frontend 19건·타입/build(617.76 kB)·Ruff, 실제 CUDA/Chrome에서 0.20→1.00→0.20 변경에 따른 검출 있음→0명→복구와 동일 세션·기존 이벤트 유지·입력/모바일/새로고침을 확인했다. 검증용 설정을 원래 사용자 값으로 복원하고 임시 카메라·인물만 정리했다. 서비스 반영을 완료했으며 모델·의존성·migration 변경은 없다. 상세 내용은 [기능 설정 결과](docs/recognition-controls-report.md)에 기록했다.

2026-10-04 Live Search 화면 배치 변경·서비스 반영: 선택한 카메라의 영상 박스(Face)를 콘텐츠 맨 위에 한 열·전체 너비로 배치하고 추적별 얼굴 분석 목록을 사진과 정보를 담은 반응형 카드 격자로 변경했다. 분석 전·중지 후 검출 점수의 빈 값으로 화면 갱신이 중단되는 문제도 수정했다. frontend 논리 19건·타입/build(618.07 kB), 실제 CUDA/Chrome Live Search와 전체 페이지 다크 테마 2건을 통과했다. 데스크톱 가로 카드 배열·390 px 모바일 한 열·가로 넘침 없음·이벤트 필터·카메라 전환/URL 복원을 확인했다. 기존 카메라 3개·인물 2명·기능 설정·카메라 실행 상태를 보존하고 임시 시험 자료를 정리했다. 상세 내용은 [Phase 7 결과](docs/phase7-report.md)에 기록했다.

2026-10-04 업로드 MP4 얼굴 직접 검출·등록 인물 비교 구현 및 서비스 반영: 기능 설정에 [사람 검출 사용] 체크와 [얼굴 검출 기준](0.10~0.99), [최소 얼굴 크기](8~512 px 정수)를 추가했다. 체크를 끈 업로드 영상은 사람 검출 없이 얼굴 검출 테스트와 공유하는 전체 화면·분할 영역 검사와 정렬 조건으로 얼굴을 찾고, 공간 추적 번호와 기존 다중 사진 비교로 등록 인물 후보·검색 이벤트를 생성한다. 모드 변경 시 업로드 영상의 세션을 교체하고 검출 조건 변경 시 사진 캐시를 정리한다. 이 새 경로는 MP4에만 적용하고 Live Search의 프레임 빈도/간격·검사 인원 상한을 유지한다. backend 181건, frontend 19건·타입/build·Ruff, 실제 CUDA/Chrome 기존 3건과 신규 업로드 얼굴 모드 1건을 통과했다. 기존 카메라 3개·인물 2명·얼굴 검출 테스트 결과 5개·기능 설정·카메라 중지 상태를 보존하고 임시 시험 자료를 정리했다. 새 항목은 기본 체크/0.50/8px로 복원했으며 추가 migration·모델/의존성 변경은 없다. 상세 내용은 [기능 설정 결과](docs/recognition-controls-report.md)에 기록했다.


2026-10-04 Live Search 컬럼 재배치·서비스 반영: 상단 영상 오른쪽에 [분석할 카메라]를 배치하고 [상세 분석 지표]·[추적별 얼굴 분석]을 같은 오른쪽 컬럼에 순서대로 표시한다. 영상 아래 검색 이벤트는 가운데, CCTV 목록은 왼쪽에 배치한다. 좁은 화면에서는 두 컬럼 또는 한 컬럼으로 전환한다. 타입 검사·프로덕션 빌드(624.24 kB), Chrome 다크 테마·Live Search·업로드 얼굴 검출 및 이벤트 3건을 통과했으며 경계 너비 조정 후 Live Search와 빌드를 재검증했다. 데스크톱·1280 px·390 px 배치와 가로 넘침 없음을 확인했다. 상세 내용은 [Phase 7 결과](docs/phase7-report.md)에 기록했다.


2026-10-04 검색 이벤트 목록 3컬럼·서비스 반영: 데스크톱 3컬럼, 1280 px 이하 2컬럼, 600 px 이하 1컬럼으로 표시한다. 긴 이름과 상태는 줄바꿈하며 사진 크기를 카드에 맞춘다. Chrome Live Search 브라우저 시험과 프로덕션 빌드(624.33 kB)를 통과했다. 상세 내용은 [Phase 7 결과](docs/phase7-report.md)에 기록했다.


2026-10-04 Live Search 화면 정리·서비스 반영: 다른 계정의 영상 접근 권한 입력과 왼쪽 CAMERAS 박스를 제거하고 카메라 선택 상자로 전환하도록 정리했다. 조작 가능한 [분석 중지] 버튼은 빨간색, 비활성 상태는 회색으로 표시한다. 검색 이벤트 목록은 데스크톱 4컬럼, 1280 px 이하 2컬럼, 600 px 이하 1컬럼으로 표시한다. 타입 검사·빌드(618.83 kB)·프런트엔드 19건·Chrome 다크 테마/Live Search/업로드 얼굴 분석 3건을 통과했다. 상세 내용은 [Phase 7 결과](docs/phase7-report.md)에 기록했다.


2026-10-04 전체 로그 삭제 구현·서비스 반영: 로그 페이지에 관리자용 [전체 로그 삭제] 버튼을 추가했다. 확인 후 검색 조건과 관계없이 전체 얼굴 검사 로그를 삭제하고 건수·목록·집계·페이지를 갱신한다. 관리자/CSRF 검사·삭제 감사 기록·삭제 중 중복 조작 방지·실패 처리를 적용했다. 임시 DB의 관련 backend 20건, Ruff, 타입 검사·빌드(621.16 kB), Chrome 삭제 UI 및 다크 테마 검증을 통과했다. UI 삭제는 모의 응답으로 검증하여 기존 저장 로그를 보존했다. API 서비스를 재시작해 반영했으며 migration 변경은 없다. 상세 내용은 [기능 설정·로그 결과](docs/recognition-controls-report.md)에 기록했다.


2026-10-04 Live Search 카메라 제목·파일명·필터 배치 구현 및 서비스 반영: [분석할 카메라] 제목을 [카메라]로 변경하고 업로드 원래 이름을 시험 MP4 업로드 아래에 표시한다. nullable cameras.video_filename을 추가하는 DB migration 0008_video_filename을 적용했으며 이름은 카메라 전환·새로고침 뒤에도 유지한다. 이전 업로드는 원래 이름이 기록되지 않아 [파일명 정보 없음]으로 표시하고 다시 업로드하면 갱신된다. 검색 이벤트 필터 3개는 한 줄, 모바일은 한 열로 표시한다. 전체 backend 183건·MariaDB 통합 1건·Ruff·타입/build(621.51 kB)·Chrome 5개 시험을 통과했고 기존 기능 설정 시험을 얼굴 전용 모드에서도 동작하도록 보완했다. API와 프런트엔드 반영을 완료했다. 상세 결과는 [Phase 7 결과](docs/phase7-report.md)에 기록했다.


2026-10-04 영상·검색 이벤트 간격 고정·서비스 반영: 영상과 검색 이벤트를 같은 왼쪽 컬럼에 세로로 묶어 오른쪽 상세 분석 지표·얼굴 분석의 높이와 독립되게 배치했다. 지표를 펼치고 접거나 모바일 한 컬럼으로 전환해도 영상 아래 18 px 간격을 유지한다. 타입 검사·프로덕션 빌드(621.35 kB)·Chrome 다크 테마/Live Search 2개 시험을 통과했다. 상세 내용은 [Phase 7 결과](docs/phase7-report.md)에 기록했다.


2026-10-04 4200포트 외부 접속 설정: frontend 바인딩을 0.0.0.0:4200으로 변경하고 로컬 ALLOWED_ORIGINS에 외부 인터페이스 주소를 추가했다. backend·frontend를 재시작하여 화면·health·인증 API·실제 Chrome 로그인/WebSocket/로그아웃을 해당 주소에서 확인했다. 검증은 서버에서 진행했으며 별도 외부 장치 경로는 미확인이다. UFW가 활성화되어 있으나 sudo 비밀번호 요구로 방화벽 허용 규칙의 확인·변경은 미완료다. 필요 시 서버에서 sudo ufw allow 4200/tcp를 실행한다. 상세 결과는 [Phase 7 결과](docs/phase7-report.md)에 기록했다.


2026-10-05 검색 이벤트 검출 프레임 레이어 팝업·서비스 반영: Live Search의 [검출 프레임]을 같은 페이지의 레이어 팝업으로 표시한다. 인증 이미지를 화면 크기에 맞추고 후보·카메라·시각 정보를 표시하며 닫기 버튼/Esc/배경 클릭·초점 복원·로딩/만료 오류를 지원한다. 프런트엔드 19건·타입/build(624.93 kB)·Chrome Live Search/다크 테마/기존 이벤트 및 재연결 3개 시험을 통과했다. 별도 API나 migration 없이 프런트엔드 반영을 완료했다. 상세 결과는 [Phase 7 결과](docs/phase7-report.md)에 기록했다.


2026-10-05 사람·얼굴 독립 FPS·모든 프레임 검사 구현 및 서비스 반영: 기능 설정을 사람 검출·얼굴 검출·등록 인물 비교 영역으로 나누고 사람 1~240 FPS, 얼굴 0.1~240 FPS와 각각의 모든 프레임 검사 체크를 추가했다. 검출 주기를 분리하고 사람 검출 사이에는 추적 위치를 예측한다. 모든 프레임 옵션을 사용하는 업로드 MP4는 GPU가 느려도 프레임을 누락하지 않고 종료·반복 직전까지 순서대로 처리한다. 기존 얼굴 간격은 FPS로 변환해 보존하며 별도 migration은 없다. 전체 backend 188건·MariaDB 통합 1건·frontend 19건·Ruff·타입/build(628.26 kB)·Chrome/CUDA 신규 빈도 및 기존 기능 설정/업로드 얼굴 이벤트/다크 테마 4개 시험을 통과했다. API·worker를 재시작해 반영하고 기존 카메라·설정·중지 상태를 보존했다. 상세 결과는 [기능 설정 결과](docs/recognition-controls-report.md)에 기록했다.


2026-10-05 Live Search 검출 기능 선택·서비스 반영: [분석 시작] 버튼 위에 사람 검출 사용·얼굴 검출 사용 체크를 배치하고 기능 설정의 사용 여부 체크를 제거했다. 업로드 MP4에서 사람만/얼굴만/둘 다 선택하고 두 기능 해제 시 시작을 막는다. 선택은 카메라 분석 세션에 적용하며 분석 중 잠금·새로고침 복원을 지원한다. 사람만 분석은 얼굴 검사·인물 비교·검색 이벤트·얼굴 로그를 건너뛰고 얼굴만 분석은 기존 등록 인물 비교와 이벤트 경로를 유지한다. 독립 FPS·모든 프레임·검출 및 비교 기준은 기능 설정에 유지한다. 전체 backend 195건·MariaDB 통합 1건·frontend 19건·Ruff·타입/build(630.67 kB)·Chrome/CUDA 5개 시험을 통과했다. API·worker를 재시작해 반영했으며 migration 없이 기존 카메라·설정·중지 상태를 보존했다. 상세 결과는 [기능 설정 결과](docs/recognition-controls-report.md)에 기록했다.


2026-10-05 사람 검출 이미지·독립 얼굴 분석 구현 및 서비스 반영: Live Search의 추적별 분석 박스를 사람·얼굴 카드 목록으로 나누고 검출한 사람의 몸 영역 이미지를 표시한다. 얼굴 검출을 꺼도 사람 이미지를 확인할 수 있고, 두 기능을 선택한 업로드 MP4에서는 사람 검출과 관계없이 전체 영상에서 얼굴을 찾아 별도 추적·비교·이벤트를 처리한다. 독립 FPS와 카메라별 선택을 유지하며 인증된 세션 이미지 조회·제한된 메모리 캐시·중지/종료/반복 정리를 적용했다. 전체 backend 198건·MariaDB 통합 1건·frontend 19건·Ruff·타입/build(633.11 kB)·Chrome/CUDA 6개 시험을 통과했다. API·worker를 재시작해 반영했으며 migration 없이 기존 카메라·설정·중지 상태를 보존했다. 상세 결과는 [기능 설정 결과](docs/recognition-controls-report.md)에 기록했다.


2026-10-05 대시보드 카메라 목록 구현 및 서비스 반영: 등록된 모든 카메라를 데스크톱 한 행 3개, 태블릿 2개, 모바일 1개로 배치했다. 사용 여부·분석 상태·입력 유형/파일명·처리 프레임·현재 사람 추적·누적 얼굴 검출·실제 FPS·해상도·장치·마지막 프레임을 표시하고 Live Search 이동을 제공한다. 기존 인증/카메라 조회 권한을 유지하며 상태 10초·목록 30초 갱신, 최대 4개 동시 상태 요청·세션 변경 응답 무효화·조회 실패/미분석/빈 목록 표시를 적용했다. worker 메모리에 남은 세션 정보만 표시하며 분석을 자동 시작하지 않는다. 프런트엔드 19건·타입/production build·Chrome 대시보드 모의 상태/권한/갱신/반응형, 실제 등록 카메라 조회/이동, 기존 전체 페이지 다크 테마 시험을 통과했다. 프런트엔드 개발 서비스에 반영했으며 backend/worker/migration 변경은 없다.


2026-10-05 RTSP 사람 검출 전용 구현 및 서비스 반영: 실제 분석 입력이 RTSP이면 사람 검출만 사용하도록 Live Search·공개 API·worker API·CameraRun에 적용했다. 얼굴 검출 체크를 해제/잠금하고 얼굴 검사·특징 생성·등록 인물 비교·검색 이벤트 생성을 건너뛴다. 이전 요청의 얼굴 사용 값도 RTSP에서는 비활성화하며 사람 검출 해제 요청은 거부한다. MP4 선택 경로는 유지한다. RTSP 복구 검증을 사람 이미지·추적·세션 정리 기준으로 갱신했다. 전체 backend 205건·MariaDB 통합 1건·frontend 19건·Ruff·타입/build(641.66 kB)·Chrome/CUDA RTSP 복구 및 MP4 독립 빈도/등록 인물 이벤트 3개 시험을 통과했다. API·worker를 재시작해 반영했으며 기존 5개 카메라의 중지 상태와 설정을 보존했다. migration 변경은 없다.
