# Phase 1 결과

검증일: 2026-10-02. 비상업 시험용 목적을 유지하고 Phase 1 범위만 구현했다.

## 구현 및 변경 파일

- `backend/app/`: 설정/secret 관리, 로그인/logout/me, cookie session·CSRF·role 검사, RTSP 암호화 및 카메라 CRUD, liveness와 서비스/GPU 상태, rotation 로그.
- `backend/migrations/`, `backend/alembic.ini`: 사용자, 세션, 카메라, 감사 로그 및 Alembic 초기 migration.
- `backend/tests/`: 격리 API 테스트와 opt-in 실제 MariaDB/Qdrant 통합 테스트.
- `frontend/`: Angular/Bootstrap 로그인·대시보드·카메라 관리, 개발 proxy, 고정 package lock 및 실제 Chrome E2E 테스트.
- `scripts/`: private 환경/관리자 초기화, 계정 생성, read-only DB 검사, 공식 Qdrant 설치/실행, 서비스 및 실제 GPU 검증, systemd user service 설치.
- `config/qdrant.yaml`, `deploy/systemd/`, `deploy/nginx/`: native Qdrant 및 API/프런트엔드 서비스 설정, 별도 Nginx 예제.
- `.gitignore`, `.env.example`, `requirements.txt`, `requirements.lock`, `pyproject.toml`, `README.md`, `docs/`: 설치·실행·설계·라이선스·검증 문서.

`.env`, DB 연결 문서, venv, private 초기 계정, data/log/model/runtime 산출물은 Git에서 제외했다. 기존 시스템 MariaDB/Nginx 설정과 기존 파일을 덮어쓰지 않았다. Git commit은 만들지 않았다.

## 실제 검증 결과

| 검증 | 결과 |
| --- | --- |
| API 인증·권한·CSRF·CRUD·암호화·만료·validation 및 설정 secret 비노출 | 10 tests passed |
| 실제 MariaDB migration·인증·카메라 CRUD + Qdrant 연결 | 1 integration test passed |
| native Qdrant collection 생성/upsert/query/retrieve/delete 및 정리 | passed, server 1.19.1 |
| PyTorch CUDA tensor/MatMul | passed, RTX 3070 Ti |
| ONNX MatMul 결과 및 profiling | passed, CUDA node 실행 3개 |
| Angular production build | passed, 초기 bundle 443.38kB |
| TypeScript typecheck | passed |
| 실제 Chrome 로그인·서비스 상태·카메라 등록/수정/삭제·logout | 1 E2E test passed |
| Ruff 검사 | passed |
| Python dependency 검사 | passed |
| requirements.lock의 설치 버전 일치 dry-run | passed |
| 실제 DB와 모델의 Alembic schema 일치 검사 | passed, 추가 upgrade 작업 없음 |
| 문서 링크·secret 파일 mode·프런트엔드 lock 일치 | passed |

처음에는 sandbox가 network/GPU device/비동기 local socket 접근을 차단했다. 필요한 검증은 sandbox 밖에서 다시 실행하여 성공했다. 이를 CPU fallback 성공으로 바꾸지 않았다. 테스트에서 Starlette의 httpx TestClient 사용에 대한 비차단 deprecation warning이 1개 발생했다.

GPU 보고서는 `data/reports/gpu.json`, 실제 서비스 검증 보고서는 `data/reports/services.json`, 브라우저 캡처는 `data/screenshots/dashboard.png`에 있다. CUDA 보고서는 실제 검증 시각을 포함하며 UI에서 최근 기록으로 표시한다.

## 실행 상태 및 명령

localhost:4200에 Angular 화면, localhost:8000에 API, localhost:6333에 native Qdrant를 systemd 사용자 서비스로 실행했다. 초기 로그인 정보는 private `data/local-admin.txt`에서 확인한다.

```bash
systemctl --user status cctv-backend cctv-qdrant cctv-frontend
.venv/bin/python scripts/check_services.py
.venv/bin/python scripts/check_gpu.py
.venv/bin/pytest -q -m 'not integration'
CCTV_RUN_INTEGRATION=1 .venv/bin/pytest -q -m integration
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

## 다음 단계

Phase 2에서는 MP4 virtual camera 입력, 단일 GPU inference worker의 명령/상태 내부 HTTP, 공유 YOLO detector, camera별 tracker와 session lifecycle, annotation MJPEG 미리보기, 1채널 초기 benchmark를 구현한다. 실제 YOLO 가중치 선정과 사용 조건 확인 및 CUDA 추론 검증도 이 단계에서 수행한다.

현재 CCTV capture/inference, 얼굴 등록·검색, MatchEvent/WebSocket, clip/retention cleanup, calibration, Re-ID 및 multi-camera tracking은 후속 Phase 대상이다. Nginx 설정은 예제만 작성했고 기존 Nginx에 배포하거나 동작 검증하지 않았다.
