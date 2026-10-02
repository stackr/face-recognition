# Phase 2 검증 결과

2026-10-02, Ubuntu 24.04.2 / Python 3.12.3 / RTX 3070 Ti 8GB 환경에서 검증했다. 프로젝트는 비상업 시험용이다.

## 구현 결과

- 단일 native GPU worker, 공유 YOLO11n person detector, 카메라별 ByteTrack/capture/latest-frame buffer를 구현했다. API는 GPU 모델을 로딩하지 않는다.
- MP4 virtual camera 생성·업로드·반복/종료 및 기본 RTSP 연결을 지원한다. 등록되어 있던 카메라의 주소/자격 증명/설정은 보존했다.
- 카메라 시작·중지·분석 상태와 내부 service-token HTTP 통신을 구현했다. 연결 요청 수락과 실제 분석 성공 상태를 구분한다.
- 브라우저에서 분석 영상의 bbox/track ID, 처리 FPS, 서버 지연과 건너뛴 프레임을 확인할 수 있다. 여러 시청자는 동일한 JPEG cache를 재사용한다.
- 로그인/CSRF/카메라별 권한, 미리보기 동시 시청자 상한, 세션/권한 재검사, 파일 크기·저장량·잔여 디스크·해상도 제한을 적용했다.

주요 파일: `backend/app/worker/{detector,tracker,runtime,api}.py`, `backend/app/api/analysis.py`, `backend/app/services/{worker_client,camera_operations}.py`, `backend/migrations/versions/0002_video_sources.py`, `0003_camera_permissions.py`, `frontend/src/app/app.component.*`, `deploy/systemd/cctv-worker.service`, `scripts/{prepare_phase2,run_worker,check_yolo,check_camera,benchmark}.py`.

## 실제 GPU와 영상 연결

`scripts/check_yolo.py`에서 공식 시험 영상의 사람 box 4개를 검출했다. 실제 inference backend parameter의 device는 **cuda:0**, GPU는 RTX 3070 Ti였다. 모델 checksum과 배포 버전/라이선스는 [models.md](models.md)에 기록했다.

Torch 2.11.0+cu128 / torchvision 0.26.0+cu128 / Ultralytics 8.4.171 / OpenCV 5.0.0.93 / lap 0.5.12 조합을 사용한다. PyPI의 다른 torchvision 빌드와 혼용하면 `torchvision::nms` 초기화 오류가 발생하여 공식 cu128 wheel로 고정했다. `requirements.lock`에 전체 환경을 기록했다.

기존 등록 카메라에서 **1920×1080 RTSP 영상 수신과 인증된 JPEG 미리보기**를 확인했다. 최종 확인 시에는 최근 중지 요청에 따라 카메라가 중지 상태여서 그대로 유지했다. 화면의 영상 분석에서 다시 시작할 수 있다. 얼굴 또는 특정 인물 인식 정확도를 검증한 결과는 아니다. 접속 주소/계정 정보와 실제 카메라 영상은 보고서에 포함하지 않는다.

## 초기 1채널 benchmark

`scripts/benchmark.py --duration 20`을 실행했다. OpenCV 공식 vtest.avi를 MP4로 변환한 768×576 / 10 FPS 영상, detector 목표 5 FPS, worker 사전 초기화 상태다. 원본 FPS에 맞춘 실시간 재생이며 offline 최대 처리량 측정이 아니다. 측정 중 다른 카메라/미리보기 시청자는 없고, 종료 직전 인증된 미리보기를 별도 검증했다.

| 항목 | 실측 |
| --- | --- |
| 측정 시간 | 20초 |
| 수신/처리 프레임 | 200 / 99 |
| 수신/사람 탐지 FPS | 10.00 / 4.95 |
| 최신 frame 교체 / 마지막 대기 | 100 / 1 |
| 서버 처리 지연 평균 / P95 | 83.62 / 132.54 ms |
| 탐지 시간 평균 | 26.03 ms |
| 최대 동시 추적 수 | 8 |
| Torch GPU allocated / reserved | 74.1 / 120.0 MiB |
| GPU 전체 메모리 peak (NVML) | 1949.4 MiB |
| GPU utilization 평균 / peak | 7 / 14% |
| 얼굴 분석 FPS | 0 — Phase 3 미구현 |

지연은 **서버에서 디코딩한 프레임을 받은 시점부터 탐지/추적/JPEG 생성까지**다. 카메라 인코딩/네트워크·decoder 내부 대기·브라우저 지연을 포함하지 않는다. latency sample은 99개이며 deque 상한은 600개다. GPU 전체 사용량은 다른 프로세스도 포함하며 utilization은 1초마다 표본을 수집했다. Torch allocated/reserved는 전체 worker GPU footprint와 같지 않다. 최신 프레임 교체는 10 FPS 입력을 5 FPS로 분석하는 정책에 따른 생략도 포함한다. 이 수치로 1080p/4채널 성능이나 탐지/추적 정확도를 보장하지 않는다.

원본 결과는 `data/reports/phase2-benchmark.json`, 자산 manifest는 `phase2-assets.json`, 실제 YOLO 검증은 `yolo.json`, 등록 카메라의 비밀정보 없는 연결 결과는 `registered-camera.json`이다. benchmark의 임시 카메라/업로드는 종료 시 삭제했다.

## 검증

- Ruff 검사/format, Angular typecheck/production build 통과. production 초기 bundle 469.01 kB.
- 기본 테스트 **19개 통과**: 기존 인증/CRUD, IPC 실패 시 미성공 처리, MP4 제한/정리/주소 보존, 카메라 grant와 해제, 미리보기 슬롯·로그아웃 재검사, 낮은 점수의 ByteTrack association, camera-local ID, 실제 시간 기준 만료, session reset, latest-frame 상한, MP4 반복/종료.
- 실제 MariaDB/Qdrant 통합 테스트 **1개 통과**, Alembic head `0003_camera_permissions`, schema drift 없음. 테스트는 자신의 임시 row만 정리했다.
- Chrome 브라우저 테스트 **2개 통과**: 기존 로그인/카메라 CRUD 및 MP4 업로드→CUDA 분석→JPEG 표시→중지→새 session으로 재시작. 비로그인 preview 401과 선택된 카메라 표시/실제 분석 대상 일치도 확인했다. 화면 캡처는 `data/screenshots/phase2-live.png`다.
- 소스와 로그에서 실제 DB/세션/service secret 및 초기 계정 비밀번호가 발견되지 않았다.
- `cctv-backend`, `cctv-worker`, `cctv-qdrant`, `cctv-frontend` 사용자 서비스 실행. 기존 시스템 MariaDB/Nginx 설정은 변경하지 않았다.

검증 명령은 [README.md](../README.md)에 기록했다. Starlette TestClient의 기존 httpx deprecation warning은 남아 있으며 테스트 실패는 아니다. Nginx production 설정은 예제만 제공하고 이번에 배포하지 않았다.

## 다음 Phase

Phase 3에서 face detection/alignment/quality/ArcFace embedding과 실제 CUDA 실행을 구현하고 얼굴 분석을 포함한 benchmark를 수행한다. 현재 RTSP 연결은 open/read timeout 및 명시적 재시작만 제공한다. 자동 reconnect/backoff는 Phase 5, 다중 camera throughput/최적화는 Phase 9 범위다.
