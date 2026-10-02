# CCTV Search

비상업 시험용 CCTV 얼굴 검색 프로젝트. 현재 **Phase 4**까지 구현했다. Ubuntu native 서비스와 Python venv를 사용한다.

로그인/카메라 관리에 더해 MP4 및 기본 RTSP 입력, YOLO11n 사람 탐지, ByteTrack 추적, 인증된 MJPEG 미리보기, 카메라별 영상 접근 권한, 별도 GPU worker와 1채널 benchmark를 사용할 수 있다. SCRFD 얼굴 탐지, 5-point alignment, 품질/자세 평가와 L2 정규화된 512차원 ArcFace 특징을 생성한다. 인물·다중 얼굴 등록, Memory/Qdrant cosine 검색, 인증된 등록 이미지와 인물별 권한, 삭제 재시도 및 이미지·특징별 보관 기간 정리를 제공한다. 전체 요구 사항은 [PLAN.md](PLAN.md), 구현 구성과 후속 설계는 [architecture.md](docs/architecture.md)를 참고한다.

## 현재 실행 환경

| 항목 | 검증한 버전 |
| --- | --- |
| Ubuntu | 24.04.2 LTS |
| Python | 3.12.3 |
| Node.js / npm | 22.20.0 / 10.9.3 |
| Angular / Bootstrap | 21.2.25 / 5.3.8 |
| MariaDB | 10.11.13 |
| Qdrant server / client | 1.19.1 / 1.19.1 |
| NVIDIA GPU / driver | RTX 3070 Ti 8GB / 575.51.03 |
| PyTorch / CUDA runtime | 2.11.0+cu128 / 12.8 |
| torchvision / Ultralytics | 0.26.0+cu128 / 8.4.171 |
| OpenCV / lap | 5.0.0.93 / 0.5.12 |
| cuDNN / ONNX Runtime GPU | 9.19.0 / 1.26.0 |

Python 직접 의존성은 `requirements.txt`, 전체 해결 버전은 `requirements.lock`에 고정했다. 프런트엔드는 `frontend/package.json` 및 `frontend/package-lock.json`에 고정했다.

## 접속 및 초기 계정

- 화면: http://127.0.0.1:4200
- API health: http://127.0.0.1:8000/api/health
- API 문서: http://127.0.0.1:8000/docs

초기 계정은 `scripts/bootstrap_admin.py`가 생성한다. 로그인 정보는 **`data/local-admin.txt`**에서 직접 확인한다. 이 파일과 `.env`는 mode 0600이며 Git에서 제외된다. 인증은 HttpOnly cookie를 사용하므로 API 문서에서 보호된 API를 호출하려면 로그인 cookie와 CSRF header가 필요하다.

관리자는 카메라 관리와 분석을 사용할 수 있다. 카메라의 일반 목록은 로그인 후 조회할 수 있다. `operator`와 `viewer`의 영상/분석 상태 조회는 카메라별 접근 허용이 필요하며, `operator`는 별도의 시작·중지 권한도 필요하다. 관리자는 **영상 분석 → 다른 계정의 영상 접근 권한**에서 허용/해제한다. 추가 계정은 `scripts/create_user.py`로 생성할 수 있다.

RTSP 계정 정보는 암호화하여 DB에 저장하고 관리 목록에서는 계정 정보 및 query를 제거한다. RTSP 카메라 수정 시 전체 주소를 다시 입력한다. 카메라 수정/삭제는 실행 중인 분석을 먼저 중지한다.

## Ubuntu 설치

기존 NVIDIA driver, MariaDB 및 Nginx를 확인하고 필요한 패키지만 설치한다.

```bash
sudo apt update
sudo apt install python3-venv python3-dev build-essential mariadb-server ffmpeg nginx
```

Node.js는 Angular 21과 호환되는 버전을 설치한다. 검증 버전은 Node 22.20.0이며, Ubuntu 기본 Node 패키지를 설치하기 전에 버전을 확인한다. [Angular 버전 호환표](https://angular.dev/reference/versions)를 참고한다.

## Python venv 및 설정

프로젝트 root에서 실행한다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --no-cache-dir -r requirements.lock
.venv/bin/python scripts/bootstrap_env.py
```

`bootstrap_env.py`는 존재하는 `.env`를 유지한다. 신규 생성 시 `DBCONFIG.md`의 연결 정보와 임의의 session/내부 service secret, RTSP 암호화 key를 사용한다. `DBCONFIG.md`가 없으면 `.env`의 DB 값을 자신의 시험 환경에 맞게 설정한다. `.env`를 shell에서 source할 필요는 없다.

## CUDA/GPU 확인

NVIDIA driver가 GPU를 인식해야 한다. 검증 환경에서는 전체 CUDA toolkit 또는 `nvcc`를 추가 설치하지 않고 venv의 CUDA/cuDNN runtime을 사용했다. `requirements.lock`의 PyTorch CUDA wheel은 공식 `cu128` index에서 설치된다.

```bash
nvidia-smi
.venv/bin/python scripts/check_gpu.py
```

검증은 PyTorch GPU 행렬 연산, ONNX MatMul 수치 검증, ONNX profiling의 `CUDAExecutionProvider` node 실행을 확인한다. 결과는 `data/reports/gpu.json`에 저장하며 provider 목록만으로 성공 처리하지 않는다.

CPU 시험은 다음처럼 명시적으로 수행한다. CUDA 요청의 CPU fallback 결과는 `cpu_fallback`이며 exit code 1이므로 GPU 성공으로 표시되지 않는다.

```bash
.venv/bin/python scripts/check_gpu.py --device cpu --output data/reports/gpu-cpu.json
.venv/bin/python scripts/check_gpu.py --allow-cpu-fallback
```

실제 YOLO 및 얼굴 모델 3개의 CUDA 추론을 검증했다. 4채널 처리량은 후속 검증 대상이다. `nvidia-smi`의 driver 지원 CUDA 버전과 venv에서 사용하는 runtime 버전을 구분한다. 참고: [PyTorch 공식 설치 조합](https://pytorch.org/get-started/previous-versions/), [ONNX Runtime CUDA 요구 조건](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).

## MariaDB 설정 및 migration

현재 제공받은 DB는 연결 후 기존 table이 없는 것을 확인하고 migration을 적용했다. 다른 환경에서는 시험용 schema를 만들고 `.env`에서 해당 schema를 지정한다. 예시는 실제 비밀번호로 바꿔 사용한다.

```sql
CREATE DATABASE cctv_search_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'cctv_search'@'localhost' IDENTIFIED BY 'replace_with_a_strong_password';
GRANT ALL PRIVILEGES ON cctv_search_test.* TO 'cctv_search'@'localhost';
```

```bash
.venv/bin/python scripts/inspect_db.py
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/python scripts/bootstrap_admin.py
```

현재 migration은 `users`, `auth_sessions`, `cameras`, `audit_logs`를 생성한다. 기존 table이나 데이터를 자동 초기화하지 않는다. 다른 role의 계정은 아래 명령으로 생성하며 비밀번호는 출력되지 않는 prompt에 입력한다.

```bash
.venv/bin/python scripts/create_user.py --username reviewer --role viewer
```

## Qdrant native 설정

```bash
.venv/bin/python scripts/install_qdrant.py --version 1.19.1
.venv/bin/python scripts/run_qdrant.py
```

공식 GitHub release의 Linux x86_64 바이너리를 `.tools/qdrant/`에 설치하고 공개된 SHA256을 검증한다. 다운로드 출처와 checksum은 `.tools/qdrant/release.json`에 기록한다. native 설정은 `config/qdrant.yaml`이며 저장 경로는 `data/qdrant/`, HTTP/gRPC는 localhost의 6333/6334다. telemetry는 끈다. `QDRANT_API_KEY`를 설정하면 runner와 client에 함께 적용된다.

서비스가 실행 중일 때 다음 명령은 임의 이름의 임시 collection에서 생성/upsert/query/retrieve/delete를 검증하고 collection을 정리한다. Phase 4는 실제 `face_embeddings` collection에 등록 얼굴을 저장한다. 512차원 cosine과 모델 버전을 collection metadata로 확인하고, 소유자·모델이 다른 기존 collection을 초기화하거나 덮어쓰지 않는다.

```bash
.venv/bin/python scripts/check_services.py
```

## Angular 및 수동 실행

```bash
npm --prefix frontend ci --cache .cache/npm
npm --prefix frontend run build
npm --prefix frontend run start
```

백엔드는 별도의 터미널에서 시작한다. `.env`는 프로젝트 root 기준으로 읽는다.

```bash
.venv/bin/uvicorn --app-dir backend app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

개발 서버는 `/api`를 백엔드로 proxy하여 같은 origin의 인증 cookie를 사용한다. backend는 GPU 모델을 로딩하지 않는다. 이미 systemd 서비스가 실행 중이면 수동 명령을 중복 실행하지 않는다.

## systemd 실행

검증 환경에는 `cctv-backend`, `cctv-qdrant`, `cctv-frontend`, `cctv-worker` **사용자 서비스**를 등록하여 실행했다. 기존 시스템 MariaDB/Nginx 설정은 변경하지 않았다. 프런트엔드 사용자 서비스는 개발용 Angular 서버다.

```bash
.venv/bin/python scripts/install_user_services.py --start
systemctl --user status cctv-backend cctv-qdrant cctv-frontend cctv-worker
systemctl --user restart cctv-backend
systemctl --user stop cctv-backend cctv-qdrant cctv-frontend cctv-worker
journalctl --user -u cctv-backend -n 30 --no-pager
```

installer는 `deploy/systemd/`의 `@PROJECT_ROOT@`를 현재 절대 경로로 치환하고 `~/.config/systemd/user/`에 등록한다. 다른 내용의 기존 unit이 있으면 유지하고 중단한다. 서비스는 현재 로그인 사용자로 실행하며 프로젝트 root를 working directory로 사용하고 UMask 0077, 실패 시 restart를 적용한다. `.env`는 애플리케이션이 직접 읽는다.

사용자 서비스의 부팅/로그아웃 후 지속 실행에는 시스템의 user lingering 설정이 필요할 수 있다. GPU worker는 localhost:8001에서 실행하며 내부 service token으로만 접근한다. worker를 재시작하면 실행 중인 분석을 화면에서 다시 시작한다.

## Nginx 배포 예제

`deploy/nginx/cctv-search.conf`는 Angular production build와 `/api`, 향후 `/ws` 및 MJPEG 미리보기 proxy 설정 예제다. `@PROJECT_ROOT@` 치환 후 사용할 수 있다. 기존 Nginx에 자동 설치하지 않았으며 해당 배포는 아직 검증하지 않았다.

예제는 localhost:8080을 사용한다. 적용할 origin을 `.env`의 `ALLOWED_ORIGINS`에 명시해야 로그인/변경 요청을 허용한다. HTTPS로 배포할 때 `COOKIE_SECURE=true`를 설정하고 실제 HTTPS origin을 등록한다. 영상/얼굴 파일을 Nginx public static 경로에 추가하지 않는다.

## Phase 1 API

| Method | 경로 | 권한 |
| --- | --- | --- |
| GET | `/api/health` | 공개 liveness |
| POST | `/api/auth/login` | 계정/비밀번호 |
| GET | `/api/auth/me` | 로그인 |
| POST | `/api/auth/logout` | 로그인 + CSRF |
| GET | `/api/system/status` | 로그인 |
| GET | `/api/cameras?offset=0&limit=100` | 로그인 |
| GET | `/api/cameras/{camera_id}` | 로그인 |
| POST | `/api/cameras` | admin + CSRF |
| PUT | `/api/cameras/{camera_id}` | admin + CSRF |
| DELETE | `/api/cameras/{camera_id}` | admin + CSRF |

로그인 응답과 `/api/auth/me`는 user 정보 및 CSRF token을 반환한다. 변경 요청은 session cookie에 더해 `X-CSRF-Token` header를 전송한다. session token 자체는 DB에 SHA256으로 저장한다. 잘못된 입력의 응답에는 제출한 비밀번호/RTSP 값을 포함하지 않는다. 로그인 시도 수를 제한하며 로그인과 카메라 변경을 감사 기록으로 저장한다. application 로그는 rotation을 사용한다.

## 테스트 및 검증

```bash
.venv/bin/ruff check backend scripts
.venv/bin/pytest -q -m 'not integration'
CCTV_RUN_INTEGRATION=1 .venv/bin/pytest -q -m integration
.venv/bin/python scripts/check_services.py
.venv/bin/python scripts/check_gpu.py
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

기본 테스트는 격리된 SQLite와 Qdrant memory client로 API의 인증·권한·CSRF·CRUD·암호화를 검증한다. opt-in 통합 테스트는 migration된 실제 MariaDB와 native Qdrant를 사용하고 자신이 만든 임시 계정·카메라만 정리한다. 실제 Qdrant CRUD는 `check_services.py`에서 별도로 검증한다.

브라우저 테스트는 backend/Angular/Qdrant 서비스와 생성된 초기 계정 파일이 필요하다. 현재는 설치된 `/usr/bin/google-chrome`을 headless로 사용하며 다른 OS/브라우저 경로에서는 `frontend/playwright.config.ts`를 수정한다. trace를 저장하지 않는다. 화면 캡처는 `data/screenshots/dashboard.png`다.

검증 결과는 [phase1-report.md](docs/phase1-report.md), [phase2-report.md](docs/phase2-report.md), [phase3-report.md](docs/phase3-report.md)에 기록했다.

## MP4 / RTSP / Benchmark

모델과 공식 시험 MP4를 준비한다. 다운로드 파일은 Git에서 제외하며 출처/체크섬/사용 조건은 [models.md](docs/models.md)에 기록했다. 이미 받은 원본 파일도 고정 SHA256과 대조한다.

```bash
.venv/bin/python scripts/prepare_phase2.py
.venv/bin/python scripts/prepare_phase3.py
.venv/bin/python scripts/prepare_face_samples.py
.venv/bin/python scripts/check_yolo.py
.venv/bin/python scripts/check_face_gpu.py
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/python scripts/install_user_services.py --start
```

수동 실행 시 API와 별도의 터미널에서 worker 하나만 실행한다. systemd가 실행 중이면 중복 실행하지 않는다.

```bash
.venv/bin/python scripts/run_worker.py
```

**등록된 CCTV:** 카메라 관리에서 해당 카메라의 **영상 분석 → 분석 시작**을 누른다. 시작 응답은 연결 요청 수락을 의미하며, 영상 탐지가 성공하면 상태가 `분석 중`으로 바뀐다. 연결 실패는 화면에 별도 표시된다. 기존 RTSP 카메라에 시험 MP4를 업로드해도 등록된 CCTV 주소는 유지된다.

**MP4 시험:** 입력 유형을 `시험 영상 · MP4`로 등록하고 영상 분석에서 `data/videos/people.mp4` 또는 자신의 MP4를 업로드한다. 업로드 후 분석 입력을 MP4로 선택하고 시작한다. 반복 재생을 끄면 파일 끝에서 종료된다. 반복/재시작마다 새 영상 세션을 만들고 추적 번호를 초기화한다.

미리보기는 사람 박스/추적 번호가 표시된 분석 JPEG다. 기본 탐지와 미리보기 전달의 목표는 각각 5 FPS이며, 원본 영상의 모든 프레임을 표시하지 않는다. 얼굴 box/landmark와 추적별 최적 얼굴 및 품질 제외 사유를 확인할 수 있다. 동일인 판정은 아직 제공하지 않는다. 최대 입력은 3840×2160, 업로드는 파일당 200 MB, 영상 저장은 총 1000 MB이며 최소 잔여 디스크 500 MB를 유지한다. 동시 분석 기본 상한 4대는 자원 제한이고 4채널 처리 성능 보장은 아니다. 미리보기는 카메라당 4명/전체 16명으로 제한한다.

초기 계정 파일을 이용하는 로컬 검증 명령이다. 비밀번호는 출력하지 않는다.

```bash
# 다른 분석을 중지한 상태에서 실행: 임시 MP4 카메라를 생성하고 종료 시 삭제
.venv/bin/python scripts/benchmark.py --duration 20
# 카메라 식별 정보만 확인; 실제 주소/계정 정보는 출력하지 않음
.venv/bin/python scripts/check_camera.py
# 출력된 ID를 사용하여 연결/미리보기 시험; 기본적으로 시험 후 중지
.venv/bin/python scripts/check_camera.py 3
# 진행 중인 분석을 변경하지 않고 상태만 조회
.venv/bin/python scripts/check_camera.py 3 --status-only
```

benchmark는 실제 영상 FPS로 재생하며 서버 수신→탐지/추적/JPEG의 평균/P95 지연, 처리 FPS, 최신 프레임 교체 수, Torch GPU 메모리와 NVML GPU 사용량을 `data/reports/benchmark.json`에 기록한다. 과거 Phase 2 보고서는 `data/reports/phase2-benchmark.json`에 남겨 두었다. 성능 측정 구간에는 미리보기 시청자가 없고 종료 직전에 인증된 JPEG 전달을 별도 검증한다. 현재 worker는 얼굴 분석도 실행하며 해당 비용을 포함한다. 브라우저 지연과 카메라 인코더 지연은 포함하지 않는다. 과거 Phase 2 측정에는 얼굴 분석이 없었다. offline 최대 처리량과 다중 채널 측정은 아직 구현하지 않았다.

| Method | 영상 분석 경로 | 권한 |
| --- | --- | --- |
| PUT | `/api/cameras/{id}/video` | admin + CSRF; raw `video/mp4` body |
| POST | `/api/cameras/{id}/start` | admin 또는 해당 카메라 조작 권한의 operator + CSRF |
| POST | `/api/cameras/{id}/stop` | 위와 동일 |
| GET | `/api/cameras/{id}/status` | admin 또는 해당 카메라 조회 권한 |
| GET | `/api/cameras/{id}/preview` | 위와 동일; 활성 카메라의 MJPEG |
| GET | `/api/cameras/{id}/faces/{track_id}?stream_session_id={session}` | 위와 동일; 현재 세션의 정렬된 최적 얼굴 JPEG |
| PUT | `/api/cameras/{id}/access` | admin + CSRF; username/can_view/can_operate |

`start` body는 `{"source_type":"mp4","loop":true}` 또는 `{}`다. 입력 URL이나 파일 경로를 브라우저에서 지정할 수 없다. 내부 `/internal/*`는 API나 Nginx에서 공개하지 않는다. 로그아웃/계정 비활성화/권한 해제 후 기존 미리보기는 최대 2초 주기로 권한을 재검사하여 닫힌다. 전송은 한 프레임씩 소비하며 느린 시청자용 프레임 큐를 쌓지 않는다.

현재 RTSP는 기본 연결과 read/open timeout만 제공한다. 자동 재연결/backoff는 Phase 5, 다중 camera 최적화는 Phase 9에서 추가한다.

## 얼굴 분석 및 Phase 3 시험

얼굴 모델은 worker 한 곳에서 공유한다. 기존 설치에서는 모델 준비 후 `systemctl --user restart cctv-backend cctv-worker`로 코드를 반영한다. 카메라를 자동으로 시작하지 않으며 등록된 RTSP/계정/카메라 설정은 유지한다. `.venv/bin/python scripts/check_phase3.py`는 worker startup 완료를 기다린 뒤 실제 모델 장치와 서비스 상태를 읽기 전용으로 확인한다.

카메라 관리에서 **영상 분석 → 분석 시작**을 누르면 사람 추적에 얼굴 분석이 추가된다. **추적별 얼굴 분석**에 현재 품질·제외 사유·자세와 최적 얼굴 썸네일이 표시된다. `얼굴 특징 준비됨`은 특징 생성 상태이며 인물을 식별한 결과가 아니다. 시험 MP4에는 `data/videos/face-smoke.mp4`를 사용할 수 있다.

기본값은 track별 최소 0.5초 간격, frame당 최대 4 ROI, camera당 얼굴 cache 최대 100 track이다. 원본 얼굴 최소 변 길이 80px, Laplacian variance 60 이상, 평균 밝기 35~220, yaw/pitch/roll 절대값 최대 40/30/35도 및 품질 0.7 이상을 요구한다. 정렬과 landmark geometry는 가림 가능성을 검사하는 heuristic이며 정확한 가림 classifier로 검증된 것은 아니다. 크기/자세 등의 hard gate에 미달하면 품질 점수가 높아도 ArcFace를 실행하지 않는다.

같은 track에서 통과한 얼굴의 품질이 개선됐을 때만 embedding을 생성한다. 특징은 512차원 float32/L2 normalized이며 버전은 `buffalo_l-v0.7-w600k_r50-4c06341c33c2`다. 실시간 track 벡터는 브라우저/상태 API에 전달하지 않고 worker 메모리에만 유지한다. 등록 얼굴 벡터는 Phase 4의 private DB/Qdrant에 저장한다. 중지/오류/EOF/반복/재시작에서 해당 세션의 이미지·특징 cache를 없애고 lost track은 3초 후 만료한다. 썸네일은 인증·카메라 grant와 필수 session ID를 검사하며 public static 경로가 아니다.

```bash
.venv/bin/python scripts/check_face_gpu.py
# 별도 worker 스레드에서 80회 반복: 기능/메모리 회귀 시험이며 처리량 benchmark가 아님
.venv/bin/python scripts/check_face_pipeline.py
# 다른 카메라 및 GPU 검증 작업을 실행하지 않은 상태에서 순서대로 실행
.venv/bin/python scripts/benchmark.py --video data/videos/face-smoke.mp4 --duration 20 --require-embeddings --output data/reports/phase3-benchmark.json
```

benchmark는 입력 1280×720/10 FPS, detector 640, 얼굴 detector 320, 실제 모델 SHA/device와 사람 수·ROI 요청/품질 통과·제외/특징 생성 수, 처리 지연 및 GPU 자원을 기록한다. 측정 중 다른 카메라가 시작되면 중단하고 자신이 만든 임시 카메라만 정리한다. Torch 메모리 수치는 ONNX Runtime allocation을 포함하지 않으므로 NVML 전체 GPU 사용량도 함께 기록한다. `face_analysis_fps`는 ROI를 한 번 이상 검사한 frame/초이고 `face_roi_fps`는 person ROI 검사/초이며 ArcFace 특징 생성 횟수와 구분한다.

[calibration-data.md](docs/calibration-data.md)는 촬영 group 단위 분리, 수동 출현 구간/subject 정답, positive/negative pair의 검증 형식을 설명한다. 준비된 공개 사진·밝기 변형·반복 MP4는 smoke 자료다. 독립 정확도 평가 자료가 없어 검색 threshold 보정과 FAR/FRR은 `unavailable`이다.

## 인물 등록 및 Phase 4 시험

**인물 관리** 첫 화면에는 검색 대상 인물 목록을 표시한다. **인물 추가**를 눌러 모달에서 이름을 입력하고 **인물 저장 → 얼굴 사진 추가** 순서로 등록한다. 저장 후에도 모달을 유지하며 사진 등록과 **사진으로 시험 비교**를 같은 모달 안에서 진행한다. 기존 인물은 목록의 **수정** 버튼으로 모달을 열어 정보와 등록 얼굴을 관리한다. JPEG/PNG 여러 장을 선택하면 한 장씩 처리한다. 사진은 각 10 MB 이하, 가로·세로 4096px 이하, 1200만 픽셀 이하이며 한 사람만 있어야 한다. 기존 얼굴 품질 기준을 적용하고 얼굴 없음·여러 얼굴·품질 미달은 이유를 표시한다. 인물당 기본 최대 20개, 전체 최대 100명이다. 등록 이미지·시험 비교 사진의 원본은 저장하지 않으며, 품질을 통과한 정렬 얼굴 112×112 JPEG만 private `data/references`에 보관한다.

**사진으로 시험 비교**는 admin/operator에게 제공한다. 사용 중이며 해당 계정에 인물 조회 권한이 있는 얼굴만 검색한다. 점수는 한 인물의 여러 등록 얼굴 중 최대 cosine similarity다. 0.75 이상을 `유사도 후보`로 표시하고 아래 점수도 시험 비교 결과에서 구분한다. 확정 신원이나 정확도 보정 결과가 아니다. 영상 분석의 추적별 얼굴에도 같은 후보가 표시되며 이벤트 저장/알림·확인/거부 기능은 후속 Phase 6~7에 구현한다.

관리자는 인물 수정에서 `검색 대상 사용`을 끄거나 인물·얼굴을 삭제할 수 있다. 일반 계정은 목록의 **인물 보기**로 조회 모달을 열며, 인물별 grant가 있어야 정보·사진·후보 이름을 제공한다. `다른 계정의 인물·이미지 접근 권한`에서 사용자 아이디로 허용/해제한다. 카메라 영상 권한과 인물 권한은 각각 검사한다. 이미지 조회는 cookie 인증 API를 사용하며 공개 static URL이 아니다. 등록/수정/삭제/권한/사진 조회/시험 검색/만료 정리를 감사 기록에 남긴다.

등록 얼굴 특징은 512D L2 정규화 후 Fernet 암호화하여 MariaDB에 저장하고 Qdrant private collection에도 반영한다. 암호화 키는 현재 `RTSP_ENCRYPTION_KEY`를 공유하므로 기존 키를 변경하면 재암호화/재등록 절차가 필요하다. Qdrant는 localhost 전용이다. 모델 버전이 다른 특징은 검색에 섞지 않는다. `FACE_SEARCH_PROVIDER=auto`는 등록 얼굴 200개 이하에서 Memory, 초과 시 Qdrant를 사용한다. `memory`/`qdrant`로 고정할 수도 있다. 현재 Qdrant는 모든 유효 reference를 exact 검색 후 같은 인물별 집계 규칙을 적용한다. 큰 규모 ANN 최적화는 후속 범위다.

이미지와 특징은 기본 각각 30일 보관하고 `REFERENCE_IMAGE_RETENTION_DAYS` / `REFERENCE_EMBEDDING_RETENTION_DAYS`로 따로 설정한다. 설정은 새 등록 시 적용한다. 만료 즉시 조회/검색에서 제외하고 기본 30초 cleanup이 파일/vector/DB/cache를 정리한다. 이미지가 먼저 만료되면 특징 검색은 유지할 수 있고, 특징이 먼저 만료되면 아직 유효한 이미지는 조회할 수 있다. `REFERENCE_STORAGE_MAX_MB=200` 및 최소 잔여 디스크 500 MB를 적용한다.

변경 시 DB revision과 durable job을 먼저 commit하고 worker cache ACK, Qdrant 및 파일 정리를 확인한다. 동기화 실패는 `202`와 `반영 재시도 중` 상태로 표시하며 자동 backoff 또는 **반영 재시도**로 처리한다. 삭제·비활성화·만료 대상은 작업 실패 중에도 SQL 기준으로 검색에서 제외한다. 여러 저장소 작업을 하나의 원자적 transaction으로 취급하지 않는다. 프로세스가 파일 생성 후 DB commit 전에 종료해서 남긴 파일은 1시간의 유예 후 cleanup이 정리한다. 완료된 maintenance job은 7일 뒤 삭제하며 감사 기록은 유지한다.

기존 설치에서는 아래 순서로 schema와 서비스를 갱신한다. 재시작 스크립트는 실행 중이던 카메라만 재개하고 중지 카메라와 등록 설정을 유지한다.

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/python scripts/restart_analysis.py
# 공개 smoke 사진으로 임시 인물/MP4를 만들고 끝난 후 자신이 만든 데이터만 삭제
.venv/bin/python scripts/check_phase4.py
cd frontend
npx playwright test
```

검증 범위와 한계는 [phase4-report.md](docs/phase4-report.md)를 참고한다. RTSP 자동 재연결/backoff는 다음 Phase 5 범위다.

## Troubleshooting

- `Operation not permitted`, 로컬 연결 실패 또는 GPU unavailable: 실행 환경이 network/GPU device 접근을 차단하는지 확인한다. 이번 검증에서는 Codex sandbox 밖에서 native service/GPU/브라우저 검증을 실행했다.
- DB 503: `.env` 연결 정보, MariaDB 실행 여부 및 migration 적용을 확인한다. credentials를 로그에 출력하여 확인하지 않는다.
- Qdrant 연결 실패: user service 상태, localhost:6333 충돌, binary/version 일치 여부를 확인한다.
- 로그인 후 변경 요청 403: 동일 origin proxy, `ALLOWED_ORIGINS`, CSRF header 및 admin role을 확인한다.
- GPU 항목은 실시간 utilization이 아니라 마지막 저장된 검증 결과다. 환경 변경 후 `check_gpu.py`를 다시 실행한다.
- CUDA provider 목록이 있어도 검증 실패: CUDA/cuDNN 실제 라이브러리와 선택한 wheel의 호환성을 확인한다. CPU fallback을 GPU 성공으로 바꾸지 않는다.
- 비밀번호를 분실한 경우 기존 계정을 자동 덮어쓰지 않는다. `create_user.py`로 새 관리자 계정을 생성한 뒤 접속한다.

- `torchvision::nms` 오류: CPU/다른 CUDA 버전의 torchvision과 섞이지 않도록 `torch==2.11.0+cu128`, `torchvision==0.26.0+cu128`을 함께 설치한다. `requirements.lock`은 공식 cu128 index를 지정한다.
- 분석 연결 실패: `systemctl --user status cctv-worker`, `logs/worker.log`, 등록된 CCTV의 접속 정보/네트워크를 확인한다. decoder 원문 stderr는 주소 유출을 막기 위해 숨기고 로그에는 카메라 ID와 실패 코드만 기록한다.
- 다른 계정에서 영상 403: 관리자가 해당 카메라의 조회/조작 권한을 허용했는지 확인한다.
