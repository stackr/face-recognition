# CCTV Search

비상업 시험용 CCTV 얼굴 검색 프로젝트. Phase 8 이벤트 영상 클립, Phase 9 다중 카메라 성능 측정·처리 개선, Phase 10 비교 기준 평가 도구, Phase 11 선택 Person Re-ID를 구현했다. DB migration head는 `0007_event_clips`다. 실제 정확도 보정은 독립 정답 자료가 필요하고 Person Re-ID는 기본 비활성 상태다. Ubuntu native 서비스와 Python venv를 사용한다.

로그인/카메라 관리에 더해 MP4 및 기본 RTSP 입력, YOLO11n 사람 탐지, ByteTrack 추적, 인증된 MJPEG 미리보기, 카메라별 영상 접근 권한, 별도 GPU worker와 1채널 benchmark를 사용할 수 있다. SCRFD 얼굴 탐지, 5-point alignment, 품질/자세 평가와 L2 정규화된 512차원 ArcFace 특징을 생성한다. 인물·다중 얼굴 등록, Memory/Qdrant cosine 검색, 인증된 등록 이미지와 인물별 권한, 삭제 재시도 및 이미지·특징별 보관 기간 정리를 제공한다. 전체 요구 사항은 [PLAN.md](PLAN.md), 구현 구성과 후속 설계는 [architecture.md](docs/architecture.md)를 참고한다.

전체 화면은 공통 다크 테마를 사용한다. 로그인, 관리 폼·표, Live Search, 로그, 기능 설정과 얼굴 크롭 화면에 같은 색상 기준을 적용한다.

얼굴 검출 테스트는 왼쪽 메뉴 마지막에서 사용한다. 파일 입력 위에서 **검출 기준**(0.10~0.99), **최소 얼굴 크기**(8~512 px), **비교점수 기준**(-1~1)을 지정하고 영상을 선택한 후 **영상 분석**을 누르면 모든 프레임의 얼굴을 추출하여 영상 내 인물별 대표 사진을 격자로 표시한다. 최소 크기는 원본 영상의 얼굴 영역에서 짧은 변을 기준으로 한다. 분석 완료 후 분리된 묶음의 최종 특징을 다시 비교해 기준 이상인 묶음을 합치고 가장 선명한 사진을 남긴다. 같은 프레임에 함께 나온 얼굴은 합치지 않는다. 영상별 적용값과 병합 전후 묶음 수를 저장·표시하며 분석 진행률·결과 복구·이전 영상 선택·분석 중 전체 삭제를 제공한다. 기본 업로드 200 MB/60분/4K 이하, 사진과 결과 보관 24시간이다. 계정별 결과를 분리하며 영상 원본은 분석 후 삭제한다. 구현·설정·검증은 [얼굴 검출 테스트 결과](docs/face-test-report.md)를 참고한다.

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

개발 서버는 `/api`와 `/ws`를 백엔드로 proxy하여 같은 origin의 인증 cookie를 사용한다. backend는 GPU 모델을 로딩하지 않는다. 이미 systemd 서비스가 실행 중이면 수동 명령을 중복 실행하지 않는다.

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

**등록된 CCTV:** 카메라 관리에서 해당 카메라의 **영상 분석 → 분석 시작**을 누른다. 시작 응답은 연결 요청 수락을 의미하며, 영상 탐지가 성공하면 상태가 `분석 중`으로 바뀐다. RTSP 연결 실패/영상 수신 중단은 `자동 재연결 중`으로 표시하고 자동으로 다시 시도한다. 복구되면 새 영상 세션으로 분석과 미리보기를 재개한다. 재시도 중에도 **분석 중지**를 사용할 수 있다. 기존 RTSP 카메라에 시험 MP4를 업로드해도 등록된 CCTV 주소는 유지된다.

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

RTSP 연결/읽기 실패는 Phase 5의 자동 재연결/backoff로 복구한다. 다중 camera 최적화는 Phase 9에서 추가한다.

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

**인물 관리** 첫 화면에는 검색 대상 인물 목록을 표시한다. **인물 추가**를 눌러 등록 페이지에서 이름과 로컬 얼굴 사진을 입력한다. 사진에서 얼굴 영역을 크롭하고 미리보기를 확인한 뒤 **크롭한 얼굴 저장**을 누르면 인물과 얼굴을 순서대로 등록한다. 인물 정보만 먼저 저장할 수도 있다. 저장 후에는 해당 인물의 수정 페이지에서 사진 등록과 **사진으로 시험 비교**를 이어서 진행한다. 기존 인물은 목록의 **수정** 버튼으로 수정 페이지를 열어 정보와 등록 얼굴을 관리한다. 상단 **목록으로** 버튼으로 돌아가며 새로고침과 브라우저 뒤로/앞으로 가기를 지원한다. 등록 주소는 `/#/persons/new`, 수정 주소는 `/#/persons/{id}/edit`다. JPEG/PNG 여러 장을 선택하면 한 장씩 크롭 영역을 확인하고 저장하거나 취소한다. 원본 위에서 드래그해 영역을 지정하고, 영역 이동·오른쪽 아래 손잡이·좌표 입력으로 조정한다. 선택 영역은 원래 해상도의 JPEG로 전송하고 서버에서 얼굴 탐지·품질 검사·112×112 정렬을 수행한다. 실패하면 현재 크롭을 유지해 다시 조정할 수 있다. 사진은 각 10 MB 이하, 가로·세로 4096px 이하, 1200만 픽셀 이하이며 한 사람만 있어야 한다. 기존 얼굴 품질 기준을 적용하고 얼굴 없음·여러 얼굴·품질 미달은 이유를 표시한다. 인물당 기본 최대 20개, 전체 최대 100명이다. 등록 이미지·시험 비교 사진의 원본은 저장하지 않으며, 품질을 통과한 정렬 얼굴 112×112 JPEG만 private `data/references`에 보관한다.

**사진으로 시험 비교**는 admin/operator에게 제공한다. 사용 중이며 해당 계정에 인물 조회 권한이 있는 얼굴만 검색한다. 점수는 한 인물의 여러 등록 얼굴 중 최대 cosine similarity다. 기능 설정에 저장한 비교점수 기준(기본 0.75) 이상을 `유사도 후보`로 표시하고 아래 점수도 시험 비교 결과에서 구분한다. 확정 신원이나 정확도 보정 결과가 아니다. 영상 분석의 추적별 얼굴에도 같은 후보가 표시된다. Phase 6에는 이벤트 저장/알림·확인/거부를 추가하고 실제 CUDA 파이프라인 검증을 통과했다.

관리자는 인물 수정에서 `검색 대상 사용`을 끄거나 인물·얼굴을 삭제할 수 있다. 일반 계정은 목록의 **인물 보기**로 조회 페이지를 열며, 인물별 grant가 있어야 정보·사진·후보 이름을 제공한다. `다른 계정의 인물·이미지 접근 권한`에서 사용자 아이디로 허용/해제한다. 카메라 영상 권한과 인물 권한은 각각 검사한다. 이미지 조회는 cookie 인증 API를 사용하며 공개 static URL이 아니다. 등록/수정/삭제/권한/사진 조회/시험 검색/만료 정리를 감사 기록에 남긴다.

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

검증 범위와 한계는 [phase4-report.md](docs/phase4-report.md)를 참고한다.

## RTSP 재연결 및 Phase 5 시험

RTSP는 TCP로 수신하며 기본 연결 제한 시간은 5초, 프레임 읽기 제한 시간은 3초다. 연결/읽기가 실패하면 대기 시간을 1초부터 두 배씩 늘리고 최대 30초 안에서 계속 재시도한다. 기본 jitter는 20%이며 최대 간격을 넘지 않는다. 10초 동안 프레임을 계속 받으면 실패 횟수를 초기화한다. 설정은 `.env.example`의 `RTSP_*` 항목을 참고한다. 변경 후 worker/API를 재시작한다.

연결이 끊기면 대기 프레임·미리보기 JPEG·추적·얼굴 특징·검색 후보를 비우고 session UUID와 session별 frame 번호를 갱신한다. 이전 session의 늦은 추론 결과와 얼굴 이미지 요청은 폐기한다. 등록 인물의 reference는 유지한다. 재연결 대기 카메라도 동시 분석 상한에 포함된다. 입력이 최대 해상도를 초과하거나 추론 자체가 실패하면 오류로 종료하며, MP4는 RTSP 재연결 정책을 적용하지 않는다.

화면에는 재연결 대기, 재시도/복구 횟수, 처리 장치, 최근 프레임 시각을 표시한다. 중지하면 대기를 즉시 취소한다. 이미 실행 중인 OpenCV 열기/읽기는 timeout까지 기다릴 수 있다. 미리보기는 세션 변경 시 기존 연결을 끝내고 다음 상태 갱신에서 새로 연결한다. 화면 상태는 기본 2초 간격으로 갱신한다.

아래 검증은 공개 smoke MP4를 임시 localhost RTSP 서버로 송출한다. MediaMTX는 시험 도구이며 실행 서비스나 Python 의존성에 추가하지 않는다. Docker를 사용하지 않는다. 준비 단계는 고정 버전과 SHA256을 확인하고 실행 파일·MIT 고지를 `data/tools/mediamtx`에 저장한다. 검증 후 자신이 만든 서버/송출 프로세스·카메라·인물만 정리한다.

```bash
.venv/bin/python scripts/prepare_phase5.py
.venv/bin/python scripts/restart_analysis.py
.venv/bin/python scripts/check_phase5.py
npm --prefix frontend run test:e2e
```

`check_phase5.py`와 브라우저 테스트는 GPU 검증/benchmark와 동시에 실행하지 않는다. 결과는 `data/reports/phase5-native.json`, 검증 범위는 [phase5-report.md](docs/phase5-report.md)에 기록한다. 이벤트 저장·중복 방지·WebSocket의 Phase 6 구현/검증은 아래 절을 참고한다.

## 검색 이벤트 및 Phase 6 적용

Phase 6 구현과 전체 backend 회귀 91건, frontend 논리 테스트 12건, 실제 MariaDB 통합 1건, Chrome E2E 7건 및 production build를 완료했다. 실제 DB에서 `0005_match_events` head와 추가 테이블을 확인했다. 사용자 terminal의 native smoke 8개 항목도 통과했으며 detector_device=cuda:0, face_device=cuda다. 실제 이벤트 저장·인증된 WebSocket·사진 접근·중복 방지·검토·재연결 복구·새 session 이벤트를 확인했다. 초기 적용 시에는 프로젝트 root 기준으로 다음을 순서대로 실행한다.

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/python scripts/restart_analysis.py
.venv/bin/python scripts/check_phase6.py
```

적용 후 영상 분석 화면에서 최근 검색 이벤트 100건과 확인/거부를 제공한다. 후보는 같은 카메라·session·track·인물별 한 건이며 기본 30초 cooldown 후 사진 개선을 반영한다. 확인/거부는 admin 또는 해당 카메라의 운영 grant가 있는 operator만 가능하다. 조회와 사진 접근에는 카메라·인물 grant를 함께 검사한다. 사진 7일/기록 30일, 저장 한도 500 MB/50,000건이며 `.env.example`의 EVENT 설정으로 조정한다. 녹화 클립은 [Phase 8 결과](docs/phase8-report.md)의 인증된 재생·저장 한도·보관 정책을 따른다.

`GET /api/events?after_id=...`는 신규 기록, `?after_change_id=...`는 기존 이벤트의 변경도 복구한다. limit은 최대 100이며 next_cursor/has_more로 페이지를 진행한다. `/ws/events`는 cookie/Origin/권한과 제한 queue를 검사한다. nginx의 8080 origin을 사용할 경우 실제 브라우저 주소를 `ALLOWED_ORIGINS`에 추가해야 한다. API는 한 프로세스로 실행하며 migration이 없거나 DB 초기 연결이 실패하면 Phase 6 API startup을 중단한다.

API 규칙, 검증 범위 및 전체 회귀/native 적용 명령은 [phase6-report.md](docs/phase6-report.md)에 기록했다. 전체 Live Search 배치·필터 UI는 아래 Phase 7에서 제공한다.

2026-10-03 권한 변경 후 에이전트에서 전체 회귀와 실제 브라우저 검증을 완료했다. 얼굴 크롭의 실제 전송 JPEG 크기, 등록·수정·취소, RTSP 중단/복구, 이벤트 확인·거부와 WebSocket 중단 후 HTTP cursor 복구, 새로고침·모바일 표시·로그아웃을 확인했다. 기존 카메라·인물·얼굴·계정 및 카메라 실행 상태를 보존했고 자신이 만든 임시 자료를 정리했다. 결과는 `data/reports/phase6-regression.json`에 기록했으며 다음 명령으로 반복할 수 있다.

```bash
CCTV_RUN_INTEGRATION=1 .venv/bin/python -m pytest -m integration -q
.venv/bin/python -m pytest -m 'not integration' -q
npm --prefix frontend run test:e2e
```

## Live Search · Phase 7

왼쪽 메뉴의 **Live Search**를 열고 CCTV 목록에서 카메라를 선택한다. 왼쪽에서 이름/위치로 카메라를 찾고, 중앙에서 분석 시작·중지와 사람 박스/추적 번호·얼굴 후보를 확인하며, 오른쪽에서 실시간 검색 이벤트를 검토한다. 큰 화면은 3열이고 작은 화면에서는 순서대로 표시한다. 카메라 선택은 `#/live/{camera_id}`에 저장하여 새로고침·뒤로 가기에서도 복원한다. 선택만으로 분석을 시작하거나 다른 카메라 분석을 중지하지 않는다.

이벤트는 **현재 등록 얼굴**과 **검출 얼굴**을 함께 표시한다. 등록 사진은 현재 조회 가능한 대표 얼굴이므로 검출 당시 사진과 다를 수 있다. 카메라 범위, 인물 이름과 확인/거부 상태로 권한 있는 최근 100건을 필터링한다. 필터와 관계없이 WebSocket/HTTP 변경 복구를 유지한다. 사진이 만료·삭제되거나 접근 권한이 바뀌면 사진 대신 안내를 표시한다.

각 이벤트의 **삭제** 버튼으로 한 건을 삭제하거나 **목록 삭제**로 현재 필터에 표시된 삭제 가능 이벤트를 한 번에 삭제한다. 확인창에 삭제 대상과 건수를 표시하며, 삭제하면 검출 얼굴·프레임 사진도 제거한다. admin 또는 해당 카메라 운영 grant가 있는 operator에게 제공한다. 등록 인물·등록 얼굴은 유지하며, 새로고침·다른 화면·재연결에서도 삭제 상태를 복구한다. 같은 세션·추적·인물 이벤트는 다시 생성하지 않지만 새로운 세션/추적의 검출은 새 이벤트가 된다. 버튼은 현재 표시된 최대 100건을 대상으로 하므로 이후 이전 보관 기록이나 새 검출이 표시될 수 있다. 구현·검증 결과는 [이벤트 삭제 결과](docs/event-deletion-report.md)에 기록했다.

`DELETE /api/events/{event_id}`는 한 건, `POST /api/events/delete`의 `{"event_ids":[1,2]}`는 최대 100건을 삭제한다. 선택 전체의 권한을 먼저 검사하여 일부만 삭제되는 것을 막는다. `GET /api/events?after_change_id=...`와 `/ws/events`의 변경에는 `{"type":"event_deleted","event_id":1,"change_id":3}` 형식의 삭제 알림도 포함한다. 일반 목록·상세·사진 조회는 삭제된 이벤트를 반환하지 않는다.

카메라 조회/조작 grant에 맞춰 목록과 버튼을 제공하고 기존 서버 인증·CSRF 검사를 유지한다. 인물 관리 메뉴는 마지막에 유지하며 기존 등록·크롭·시험 비교 화면을 그대로 사용한다. Phase 7 구현·검증 범위와 실행 결과는 [phase7-report.md](docs/phase7-report.md)에 기록한다. 다음 개발 범위는 Phase 8 이벤트 전후 영상 클립이다.

## 얼굴 검사 로그와 기능 설정

왼쪽 **로그** 메뉴에서 카메라·검사 결과·주요 제외 사유·기간으로 기록을 조회한다. 검출 실패, 흐림·크기·밝기·자세 등의 품질 제외, 비교 사진 수집, 유사도 미달·프레임 간 불일치 및 후보 결과를 구분한다. 기록별 품질, 선명도, 밝기, 얼굴 각도, 비교 점수·사진 수, 검출 영역/입력 크기, 설정 버전과 영상 세션을 확인할 수 있다. 주요 사유 집계는 첫 번째 제외 사유 기준이며 개별 기록에는 모든 사유가 표시된다. 최신 페이지는 5초마다 자동 갱신하며 이전 기록은 cursor로 조회한다.

로그는 MariaDB에 저장하고 기본 7일/100,000건으로 제한한다. 사진·특징·인물 이름·RTSP 주소를 로그에 저장하지 않는다. 일반 계정은 사용 중인 카메라의 조회 grant가 있어야 로그를 볼 수 있다. 기록 저장은 GPU scheduler와 분리된 제한 큐에서 처리하며 DB 실패를 재시도한다. 큐 포화/재시도 실패로 생략된 수는 내부 worker 상태의 `recognition_logs.dropped`에 기록한다.

**기능 설정**에서 관리자가 사람 검출 빈도(1~15 FPS), 인물별 얼굴 검사 간격(0.2~10초), 프레임당 최대 얼굴 검사 인원(1~16명)과 비교점수 기준(-1~1, 기본 0.75)을 저장한다. 저장값은 DB에 유지하고 실행 중인 분석에도 프레임 사이에서 반영한다. 카메라 세션/추적과 이벤트 중복 방지 키를 유지하며 추적기의 시간 변환도 새 FPS에 맞춘다. 화면은 저장값과 실제 적용값을 구분한다. worker에 즉시 반영하지 못하면 `202`를 반환하고 2초 주기의 재조회로 복구한다. 다른 화면이 먼저 저장했다면 다시 불러온 후 수정하도록 한다. 최초 값은 `.env`의 5 FPS/0.5초/4명/비교 기준 0.75이며 화면에서 저장한 값이 이후 우선한다.

비교점수 기준은 Live Search의 품질 가중 평균과 최소 2장의 기준 이상 결과, 신규 이벤트 저장, 인물 관리의 사진 시험 비교에 함께 사용한다. 값을 낮추면 후보가 늘고 높이면 후보를 더 엄격하게 고른다. 변경 시 보관 중인 사진 점수를 새 기준으로 다시 판정하며 기존 검색 이벤트를 지우거나 상태를 변경하지 않는다. 로그 상세의 비교 기준은 검사 당시 적용값이다. 기존 세 항목만 저장된 DB 설정은 해당 값을 보존하고 비교 기준만 `.env`의 `FACE_MATCH_THRESHOLD`로 보완하므로 추가 migration이 필요 없다. `PUT /api/function-settings`에는 `face_match_threshold`를 포함한 네 항목과 현재 `revision`을 함께 전송한다. 상세 검증은 [비교점수 설정 결과](docs/match-threshold-report.md)를 참고한다.

얼굴 검출은 여유 영역을 포함한 머리 주변에서 시작하고 필요한 경우 사람 전체 영역으로 재검사한다. 미검출·작은 얼굴·잘린 얼굴·특징점 오류는 최대 세 번의 검사 안에서 640px 입력으로 재시도한다. 이웃 사람의 얼굴과 여러 얼굴은 기존 연결 제한을 유지한다. 품질을 통과한 최근 사진을 최대 5장 비교하며 품질에 따른 평균 점수와 최소 2장의 기준 이상 결과로 후보를 판단한다. 사진 사용 시간은 기본 3초이고 긴 검사 간격에서는 두 장을 확보할 수 있게 늘린다. 세션/추적 만료 및 큰 특징 변화에서는 사진을 비운다. 얼굴 품질 기준은 기존 설정을 사용하며 비교점수 기준은 기능 설정에서 변경한다.

움직이는 영상의 인식률이나 오인식률을 보정한 결과는 아니다. 구현·검증과 실행 명령은 [recognition-controls-report.md](docs/recognition-controls-report.md)에 기록했다.

## Troubleshooting

- `Operation not permitted`, 로컬 연결 실패 또는 GPU unavailable: 실행 환경이 network/GPU device 접근을 차단하는지 확인한다. 이전 제한 세션의 DB/Chrome 검증은 실행하지 못했으나 권한 변경 후 전체 회귀를 통과했다.
- DB 503: `.env` 연결 정보, MariaDB 실행 여부 및 migration 적용을 확인한다. credentials를 로그에 출력하여 확인하지 않는다.
- Qdrant 연결 실패: user service 상태, localhost:6333 충돌, binary/version 일치 여부를 확인한다.
- 로그인 후 변경 요청 403: 동일 origin proxy, `ALLOWED_ORIGINS`, CSRF header 및 admin role을 확인한다.
- GPU 항목은 실시간 utilization이 아니라 마지막 저장된 검증 결과다. 환경 변경 후 `check_gpu.py`를 다시 실행한다.
- CUDA provider 목록이 있어도 검증 실패: CUDA/cuDNN 실제 라이브러리와 선택한 wheel의 호환성을 확인한다. CPU fallback을 GPU 성공으로 바꾸지 않는다.
- 비밀번호를 분실한 경우 기존 계정을 자동 덮어쓰지 않는다. `create_user.py`로 새 관리자 계정을 생성한 뒤 접속한다.

- `torchvision::nms` 오류: CPU/다른 CUDA 버전의 torchvision과 섞이지 않도록 `torch==2.11.0+cu128`, `torchvision==0.26.0+cu128`을 함께 설치한다. `requirements.lock`은 공식 cu128 index를 지정한다.
- 분석 연결 실패: `systemctl --user status cctv-worker`, `logs/worker.log`, 등록된 CCTV의 접속 정보/네트워크를 확인한다. decoder 원문 stderr는 주소 유출을 막기 위해 숨기고 로그에는 카메라 ID와 실패 코드만 기록한다.
- 다른 계정에서 영상 403: 관리자가 해당 카메라의 조회/조작 권한을 허용했는지 확인한다.

얼굴 크롭 구현과 검증 범위는 [face-crop-report.md](docs/face-crop-report.md)를 참고한다.

Phase 8 이벤트 영상 클립을 서비스에 반영했다. DB head는 `0007_event_clips`이며 설치·실행·검증 및 한계는 [Phase 8 결과](docs/phase8-report.md)를 참고한다.

다중 카메라 측정은 `scripts/benchmark.py --channels 1 2 4`로 실행하며 `--mode offline_throughput`은 별도의 파일 최대 처리량 모드다. 실제 1080p 측정 조건·수치·목표 미달 항목은 [Phase 9 결과](docs/phase9-report.md)를 참고한다.

Phase 10 비교점수 평가 도구는 얼굴 쌍·영상 구간·미등록 인물 지표를 구분하며 DB 기준을 자동 변경하지 않는다. [Phase 10 결과](docs/phase10-report.md)를 참고한다. 현재 자료는 공개 smoke만 있으므로 실제 정확도 보정과 제안값은 unavailable이다.

Phase 11 선택 Person Re-ID는 `scripts/prepare_reid.py`로 가중치를 준비하고 `scripts/check_reid.py`로 실제 CUDA 경로를 검증한다. 기본 REID_ENABLED=false이며 [Phase 11 결과](docs/phase11-report.md)에 활성화·검증·복원 명령을 기록했다. 카메라 간 연관·이동경로는 Phase 12 범위다.
