# Phase 5 검증 결과

2026-10-02, 비상업 시험 환경. RTSP 자동 재연결과 제한된 지수 백오프, 최신 프레임 우선 처리, 세션별 추적·얼굴·검색 후보 초기화 및 화면의 재연결 상태 표시를 구현했다. native API/worker에 반영했으며 Docker를 사용하지 않았다.

## 구현과 사용

**카메라 관리 → 영상 분석 → 분석 시작**으로 등록된 RTSP를 분석한다. 연결/읽기 실패 시 `자동 재연결 중`을 표시하고 복구 후 분석과 미리보기를 자동으로 재개한다. 재시도 대기 중에도 **분석 중지**가 가능하다. 처리 장치, 재연결 시도/성공 횟수와 최근 프레임 시각을 함께 표시한다.

기본 설정은 TCP 수신, OpenCV FFmpeg open/read timeout 5/3초, 재시도 초기 1초/최대 30초/20% jitter, 실패 횟수 초기화를 위한 연속 수신 10초다. 최대 간격에 도달해도 재시도를 계속한다. cancel Event가 백오프 대기를 깨우며 진행 중인 열기/읽기는 timeout을 기다릴 수 있다. 재연결 중인 카메라도 동시 분석 상한에 포함하고 MP4 업로드를 차단한다. 입력 해상도 초과와 모델 추론 실패는 종료 오류로 처리한다.

실패 시 session UUID를 바꾸고 latest frame/JPEG/result/tracker/TrackFaces를 없앤다. frame 번호는 session별로 시작하고 누적 통계는 유지한다. 늦게 끝난 이전 session의 성공 결과와 추론 예외 모두 새 연결에 영향을 주지 않는다. 이전 session의 얼굴 조회는 404이며 등록 reference는 유지한다. MJPEG는 재연결/세션 변경 시 종료하고 viewer slot을 반환한다. Angular가 다음 상태 갱신에서 새 미리보기를 연결한다. 상태 갱신 간격은 기본 2초다.

검색 후보 초기화는 구현했다. 이벤트 cooldown/중복 방지는 MatchEvent가 생기는 Phase 6 범위이며 아직 구현됐다고 표시하지 않는다. 이후 dedup/cooldown key에는 `(camera_id, stream_session_id, track_id, person_id)`를 포함한다.

## 검증

| 항목 | 결과 |
| --- | --- |
| Python 전체 단위 테스트 | 54 passed, integration 1개 별도 실행 |
| 실제 MariaDB/Qdrant 통합 | 1 passed, schema head `0004_person_faces` 유지 |
| Chrome E2E | 5 passed: 로그인/카메라, MP4, 얼굴 분석, 인물 모달, RTSP 중단·복구·중지 |
| Angular production build | 통과, 초기 번들 513.27 kB |
| TypeScript / Ruff / formatting / diff whitespace | 통과 |
| native RTSP 및 실제 CUDA 분석 | 통과, detector `cuda:0`, 얼굴 모델 `cuda` |

추가 단위 테스트 9개는 재연결 시 얼굴·추적·미리보기 초기화, 이전 얼굴 조회 차단, 실제 재시도 간격 증가/최대값/안정화 후 초기화, 긴 대기 중 즉시 중지 및 추가 연결 방지, 재연결 중 admission 상한, 늦게 끝난 이전 추론의 성공·실패, 잘못된 해상도 종료, 민감한 capture 예외 비노출, MJPEG 종료 및 viewer 반환, 재연결 중 업로드 차단을 검증한다. 기존 인증·인물·사진 모달 테스트도 유지했다.

`scripts/check_phase5.py`는 공개 `face-smoke.mp4`를 임시 MediaMTX v1.21.1/FFmpeg localhost RTSP로 송출했다. 시험 인물의 reference를 등록하고 실제 CUDA 영상에서 후보를 확인했다. 서버를 SIGSTOP으로 멈춰 TCP가 유지된 상태의 프레임 수신 중단을 재현하고, 새 세션에서 자동 복구·추적 ID 1·새 얼굴 특징·후보 검색을 확인했다. 서버 종료 후 반복 연결 실패와 백오프를 확인하고 재시도 중 중지했다. 송출을 다시 시작해도 중지 상태가 유지되며 수동 분석 시작은 새 세션을 만들었다.

| native smoke 측정 | 값 |
| --- | --- |
| TCP 수신 중단부터 `reconnecting` 확인 | 9.12초 |
| 중지 전 연속 실패 횟수 | 3회 |
| 중지 직전 남은 재시도 대기 | 3.83초 |
| 백오프 중 중지 응답 | 0.006초 |
| 중지 전 수신 / 분석 / 교체된 대기 프레임 | 75 / 7 / 67 |
| 재연결 대기 중 pending frame | 0 |
| 분석 지연 평균 / P95 | 96.94 / 251.13 ms |

위 수치는 장애를 포함한 짧은 기능 시험이며 처리량 benchmark가 아니다. 읽기 호출에 지정한 3초는 전체 장애 감지 시간의 보장 상한이 아니다. 실제 TCP 중단 시험의 전체 감지 시간은 9.12초였으며 입력 버퍼와 decoder 동작까지 포함한다. 등록 얼굴 사진이나 실제 CCTV 프레임을 시험 자료로 저장하지 않았다. 시험이 만든 카메라·인물·프로세스·임시 인증서만 정리했고 기존 등록 설정과 중지 상태를 유지했다.

상세 native 결과는 private `data/reports/phase5-native.json`에 있다. Starlette/httpx deprecation, Qdrant memory exact 검색 안내와 Node 색상 환경 경고는 테스트 실패가 아니며 관련 의존성을 변경하지 않았다.

검증 스크립트의 CUDA 장치 표기, 실시간 후보 형식 및 빈 프레임 204 처리도 실제 응답에 맞춰 수정했다. 전체 E2E 중 시험 서버의 기본 10초 timeout으로 송출이 종료되는 사례가 있어 시험 서버만 read/write timeout을 60초로 조정하고 RTSP DESCRIBE 200 응답으로 송출 준비를 확인했다. 그 후 전체 Chrome 시험 5개가 통과했다. 시험 도구가 만드는 인증서는 임시 디렉터리에서 생성·삭제하도록 처리했다.

## 변경 파일과 실행

- `backend/app/worker/runtime.py`, `reconnect.py`, `api.py`: capture 재시도, 세션 상태 및 이전 결과 차단.
- `backend/app/core/config.py`, `services/worker_client.py`, `api/analysis.py`, `api/system.py`, `main.py`: 설정, 중지 timeout, 미리보기 lifecycle과 Phase 상태.
- `frontend/src/app/app.component.ts`, `app.component.html`: 재연결 안내·통계·자동 미리보기 복구.
- `backend/tests/test_reconnect.py`, `test_foundation.py`, `frontend/e2e/phase5.spec.ts`: 신규 검증과 상태 기대값.
- `scripts/prepare_phase5.py`, `rtsp_fixture.py`, `check_phase5.py`: 고정 checksum의 시험 도구와 실제 RTSP/CUDA 검증.
- `scripts/restart_analysis.py`, `check_camera.py`, `benchmark.py`, `check_phase4.py`: 재연결 상태 처리 및 기존 검증 호환.
- `.env.example`, `README.md`, `PLAN.md`, `docs/architecture.md`, `docs/models.md`, 이 문서: 설정·사용법·검증·MIT 시험 도구 고지.

```bash
# 시험 도구 준비와 기존 실행 상태를 보존하는 서비스 갱신
.venv/bin/python scripts/prepare_phase5.py
.venv/bin/python scripts/restart_analysis.py

.venv/bin/pytest -q -m 'not integration'
CCTV_RUN_INTEGRATION=1 .venv/bin/pytest -q -m integration
.venv/bin/python scripts/check_phase5.py
npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend run test:e2e
```

native GPU 검증/benchmark/브라우저 검증은 동시에 실행하지 않는다. AI 모델과 Python/Node 의존성 버전은 그대로 유지했다. MediaMTX는 시험 도구이며 archive/binary SHA256·출처·MIT 조건은 [models.md](models.md)에 기록했다. 기존 schema를 초기화하거나 사용자 데이터에 migration을 추가하지 않았다.

## 한계와 다음 단계

공개 사진과 반복 MP4의 기능 확인이며 독립 CCTV 정답을 통한 정확도 보정은 없다. FAR/FRR은 unavailable이고 유사도 후보를 동일인 확정으로 해석하지 않는다. 실제 CCTV별 인증 방식/codec/네트워크와 장시간 단절, 다중 카메라 처리량은 별도 시험이 필요하다. RTSP 재연결은 worker가 실행 중인 동안 동작하며 프로세스 장애 후 모든 카메라를 자동 재시작하는 supervisor는 범위에 포함하지 않았다.

다음은 Phase 6의 MatchEvent 저장, 인증된 WebSocket, 이벤트 중복 방지 및 DB 조회를 통한 누락 복구다. 전체 Live Search UI는 Phase 7 범위다.
