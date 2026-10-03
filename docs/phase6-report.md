# Phase 6 구현 및 검증 (2026-10-03)

MatchEvent 저장, 중복 방지, 인증된 이벤트 API/WebSocket, 재연결 복구와 운영자 확인·거부를 구현했다. **구현·서비스 반영·실제 CUDA 파이프라인 8개 항목과 전체 회귀·브라우저 검증을 완료했다.** 2026-10-03 권한 변경 후 에이전트에서 backend 91건, 실제 MariaDB 통합 1건, Chrome E2E 7건과 frontend 논리 테스트 12건을 통과했다. 실제 Alembic head는 `0005_match_events`다. 서비스 재시작과 기존 native smoke는 사용자 terminal의 통과 결과를 보존했다. 비상업 시험 목적을 유지한다.

## 구현

`0005_match_events`는 `tracks`, `match_events`, `event_state`, `event_changes`를 추가한다. 기존 테이블/데이터를 초기화하지 않는다. track의 DB 기본키와 카메라 세션 내 화면 번호를 분리한다.

worker는 유효한 후보의 메타데이터와 JPEG만 기본 32건의 별도 저장 큐에 전달한다. GPU scheduler에서 파일/SQL/알림 HTTP를 실행하지 않는다. 큐 초과와 DB/디스크 실패는 worker 이벤트 통계에 기록하고 카메라 분석은 계속한다. 저장과 HTTP 알림은 각각 최대 3회 재시도한다. 큐에 들어간 유효한 관측은 분석 중지 후에도 저장될 수 있으며 저장 시 인물/reference/gallery revision/카메라 상태를 다시 확인한다.

중복키는 `(camera_id, stream_session_id, track_id, person_id)`다. worker의 기본 30초 submission cooldown과 DB UNIQUE/잠금을 함께 적용한다. 재연결/MP4 반복/재시작과 track 만료에서 메모리 cooldown을 제거한다. 같은 키는 보관 중인 기존 이벤트를 재사용한다. 보관 기간이 지난 기록과 중복키는 cleanup에서 삭제한다. candidate의 사진 품질이 개선되거나 같은 품질에서 similarity가 높아지면 cooldown 후 같은 이벤트의 사진/프레임/점수를 같은 관측으로 교체한다. confirmed/rejected는 추론이 덮어쓰지 않는다. 검토 결과, 감사 기록과 journal을 같은 transaction에서 저장한다.

best face 생성 시 같은 원본 frame을 최대 변 1280의 JPEG로 압축한다. 한 분석 tick의 여러 얼굴은 같은 압축 frame 객체를 공유한다. 세션/track cache와 큐는 상한이 있으며 장시간 비압축 frame buffer를 추가하지 않는다. 저장 사진은 UUID JPEG이며 directory 0700/file 0600이다. commit 전 실패는 생성 파일을 정리한다. commit 응답 유실은 결과가 불확실하므로 파일을 보존하고 SQL 기반 orphan cleanup에서 처리한다. 여러 저장소의 원자적 transaction으로 간주하지 않는다.

이벤트 기록 기본 30일, 얼굴/프레임 사진 기본 7일, 저장 한도 500 MB, 기록 상한 50,000건이다. 만료는 API에서 즉시 차단하고 기본 30초마다 최대 200건을 정리한다. deleting 인물은 조회에서 즉시 제외하며 worker ACK 대기 중에도 이벤트를 정리한다. 삭제된 카메라/인물 이벤트와 1시간 이상 지난 orphan 파일도 정리한다. 비활성화한 인물의 과거 기록은 조회 권한을 가진 계정에 유지한다.

API 시작 때 journal cursor를 먼저 초기화한다. worker는 commit 후 event_id를 loopback 내부 endpoint에 전달한다. API가 1초마다 journal도 조회하므로 알림 HTTP 실패가 저장 내역의 유실을 의미하지 않는다. API/worker 재시작 후 기록은 HTTP로 복구한다. API는 기존 단일 Uvicorn 프로세스로 운영한다.

WebSocket은 허용 Origin과 로그인 cookie를 확인한다. 전송/2초 heartbeat마다 새 DB transaction에서 로그인/계정과 카메라·인물 grant를 검사한다. 권한 변경은 resync를 보내 목록을 다시 조회하게 한다. 기본 연결당 64개 ID queue, 전체 32개 연결, send timeout 5초이며 초과는 1013으로 종료한다. 세션 만료/비활성화는 1008, DB 장애는 1011이다. 브라우저에서 handshake 거절이 1006으로 보일 수 있어 HTTP 인증 검사도 수행한다. SQL은 connect 3초, socket read/write 및 pool 대기 5초로 제한했다. 실제 장애 시간/처리량은 미측정이다.

영상 분석 화면에 최근 100건, 후보 얼굴/검출 프레임, 유사도/품질/상태와 확인·거부를 제공한다. event_id로 중복을 병합하고 이전 change_id를 적용하지 않는다. 연결 중 HTTP 페이지 복구와 실시간 메시지를 함께 처리하며 WebSocket ID로 복구 cursor를 건너뛰지 않는다. 로그아웃/권한 변경 중 늦은 응답을 차단하고 30초마다 최신 목록을 조회한다. Phase 7의 전체 Live Search 배치/필터/등록 사진 비교 UI는 후속 범위다.

## API와 cursor 규칙

| Endpoint | 동작 |
| --- | --- |
| `GET /api/events?after_id=0&limit=50` | 권한 있는 보관 중 신규 event_id 오름차순 |
| `GET /api/events?after_change_id=0&limit=50` | 생성·사진 개선·확인/거부 journal 오름차순 |
| `GET /api/events?latest=true&limit=100` | event_id 내림차순 최신 목록과 event/change 초기 cursor |
| `GET /api/events/{id}` | 현재 상태 |
| `GET /api/events/{id}/face`, `/frame` | 인증/카메라/인물 grant/보관 기간 검사 후 JPEG |
| `POST /api/events/{id}/confirm`, `/reject` | CSRF/운영 권한 검사 후 상태 저장 |
| `/ws/events` | ready, person_match, heartbeat, resync |
| `POST /internal/events/notify` | loopback + service token + `{event_id}`로 journal을 깨움, 공개 proxy 없음 |

limit은 1~100이며 after_id, after_change_id, latest를 함께 사용하지 않는다. ID는 singleton event_state 행 잠금을 잡은 transaction이 발급하여 commit 순서와 일치한다. 느린 transaction이 cursor 앞쪽에 뒤늦게 이벤트를 생성하지 않으며 삭제 후 ID를 재사용하지 않는다. timestamp 순서와 ID 순서는 다를 수 있다.

응답은 items, next_cursor, has_more, cursor_kind를 포함한다. 접근 불가/만료/삭제 기록은 생략하고 next_cursor는 그 구간도 지나갈 수 있다. next_cursor로 다음 페이지를 요청한다. after_id는 기존 이벤트의 변경을 복구하지 않으므로 상태까지 복구하려면 after_change_id를 사용한다. 변경 목록에는 같은 이벤트의 현재 snapshot이 중복될 수 있다. event_id로 합치고 가장 큰 change_id를 유지하되 **복구 cursor는 응답 next_cursor로만 이동**한다. 신규 grant는 최신 snapshot 또는 cursor 0부터 다시 조회한다.

admin은 전체 조회/검토, operator/viewer는 카메라 조회 grant와 인물 조회 grant가 모두 필요하다. 검토는 admin 또는 해당 카메라 can_operate grant가 있는 operator만 수행한다. 권한 없는 개별 기록/사진은 404이며 viewer 검토는 403이다. 파일 경로/RTSP/credentials/embedding을 반환하지 않는다. 클립은 Phase 8이며 video_clip_url은 null이다.

## 실행 결과

| 검증 | 결과 |
| --- | --- |
| 신규 backend 테스트 | 27건 통과: 동시/retry dedup, cooldown, session, JPEG 일치/공유, eligibility, commit 실패/유실, retention, 권한/CSRF, cursor, WS handshake/전송/느린 연결 해제, startup cursor/알림 누락 복구 |
| 기존 독립 backend 테스트 | 설정/alignment/calibration split/vector tie 4건 통과 |
| native smoke 스크립트 회귀 테스트 | HTTP/WS 모의 환경에서 10건 통과: CUDA 장치 표기, CPU 차단, 8개 smoke 단계, 실패 단계/코드, 임시 자료 cleanup 및 비밀값 비출력; backend 격리 테스트 합계 41건 |
| 프런트엔드 Node 테스트 | event feed 7건 + 얼굴 크롭/저장 5건; 합계 12건 통과 |
| Ruff, TypeScript, Angular production build | 통과, bundle raw 529.81 kB |
| SQLite migration/schema/seed/모델 일치 | 통과 |
| MariaDB용 offline DDL | 생성 통과, `/tmp/phase6-migration.sql` |
| 실제 MariaDB 이벤트 동작 | native smoke에서 저장·조회·검토·journal 복구 통과. DB 통합 테스트에서 `0005_match_events` head와 추가 테이블 직접 확인 |
| check_phase6.py | 사용자 terminal 재실행 status=passed, native 검사 8개 항목 통과 |
| 전체 backend 회귀 | 91건 통과, 실패/오류/건너뜀 0건, 22.59초 |
| 서비스 재시작 | 사용자 terminal 결과 status=passed, resumed_cameras=[] |
| 실제 Phase 6 CUDA | detector_device=cuda:0, face_device=cuda, actual_device=cuda |
| 실제 DB 통합 테스트 | 1건 통과, migration head·추가 테이블·인증·카메라 CRUD·Qdrant 연결 확인, 0.53초 |
| Chrome E2E | 기존 6건 + Phase 6 1건, 전체 7건 통과, 실패/건너뜀/재시도 0건, 44.38초 |
| 기존 자료/실행 상태 보존 | 카메라 2개·인물 3명·얼굴 2개·계정 1개의 기존 ID 및 카메라 상태 일치, 임시 자료 잔여 0건 |
| 서비스 상태 | backend·worker·Qdrant·frontend 모두 active, `/api/health` 정상 |

최초 제한 환경에서 서비스 반영 완료 요청을 받고 2026-10-03에 실제 연결과 `alembic upgrade head`를 다시 실행했다. MariaDB와 localhost API/프런트엔드는 모두 `PermissionError(errno=1)`로 연결이 거부되었으며, `systemctl --user is-active`도 `Failed to connect to bus: Operation not permitted`로 실패했다. 당시 에이전트 실행에서는 마이그레이션이 적용되지 않았고 서비스 재시작도 진행하지 않았다. 이후 실제 반영과 native 검증은 접근 가능한 사용자 terminal에서 진행했다.

이후 사용자가 terminal에서 restart helper를 실행하여 `status=passed`, `resumed_cameras=[]`를 보고했다. 빈 목록은 재시작 전에 활성 카메라가 없어서 복원할 카메라도 없었다는 뜻이다. 첫 native smoke는 `AssertionError`로 초기 검사에서 중단되었다. YOLO가 검증해서 반환하는 장치는 `cuda:0`인데 스크립트가 `cuda`와의 동등 비교를 사용하고 있었다. detector는 `cuda` 또는 `cuda:<숫자>`를 허용하고 face model의 실제 CUDA 검사와 CPU 차단은 유지하도록 수정했다. 조건 검사를 Python assert 대신 명시적 검사로 변경하여 Python 최적화 옵션에서도 생략되지 않게 했다. 실패 시 `failed_stage`와 안전한 고정 `error_code`를 기록하고 시험 시작 이후 실패는 `failed`로 구분한다.

수정 후 사용자 terminal에서 재실행한 결과는 `status=passed`, `checked_at=2026-10-02T23:23:40.219858+00:00`(한국 시각 2026-10-03 08:23:40)다. 실제 CUDA 후보 생성, 인증된 WebSocket, 보호된 사진, session/track/person 중복 방지, 검토 저장, 오프라인 변경 복구, 검토 WebSocket 갱신, 새 session의 새 이벤트를 모두 통과했다. 검증 스크립트가 생성한 임시 카메라·인물의 삭제 요청도 오류 없이 완료했다. 이는 공개 시험 영상의 기능 검증이며 `purpose=pipeline_smoke`, `accuracy_calibrated=false`를 유지한다. 전체 회귀·브라우저 화면 검증과 실제 처리량 측정까지 통과했다는 의미는 아니다.

## 권한 변경 전 전체 회귀 및 브라우저 검증 시도 (2026-10-03)

사용자의 전체 회귀/브라우저 검증 요청에 따라 실제 실행을 시도했다. `.venv/bin/python -m pytest -m 'not integration' -vv --tb=short`는 91건을 수집했지만 첫 테스트인 `test_analysis_permissions_commands_and_secret_boundary`의 fixture 준비에서 진행하지 못해 45초 후 종료했다. 단일 테스트의 5초 faulthandler stack에서도 `TestClient.__enter__` → `portal.start_task_soon` 대기를 확인했다. 별도 진단에서 asyncio wakeup socket의 전송은 `PermissionError(errno=1)`로 거부되었다. 이 결과를 제품의 assertion 실패나 91건 통과로 처리하지 않는다.

실제 DB 통합 테스트는 1건을 실행했지만 MariaDB 연결이 `PermissionError(errno=1)`로 거부되어 실패했다. localhost API와 프런트엔드 연결도 같은 제한으로 차단되어 실제 Alembic head를 직접 조회하지 못했다. Chrome은 `setsockopt: Operation not permitted (1)`을 출력하고 `SIGTRAP`로 종료했다. 기존 브라우저 테스트 6건과 신규 Phase 6 테스트 1건 모두 browser 기동에서 실패했으며 로그인/화면 조작과 시험 데이터 생성 단계에는 도달하지 않았다.

실행 가능한 backend 격리 테스트 41건, 프런트엔드 논리 테스트 12건, 애플리케이션 TypeScript 검사와 Angular production build(529.81 kB)를 다시 통과했다. 당시 전체 회귀를 통과했다는 의미는 아니다. Ruff와 diff 검사를 통과했으며 이미 통과한 native smoke 결과는 보존했다. 제한 원인과 `status=partial` 결과는 `data/reports/phase6-regression-initial.json`에 보존하고 최종 보고서를 갱신했다.

`frontend/e2e/phase6.spec.ts`를 추가했다. 공개 시험 사진/영상과 자신이 만든 임시 카메라·인물만 사용하며 이벤트 카드, 얼굴 사진, 검출 프레임 링크, 확인·거부와 버튼 상태, WebSocket 중단 중 저장한 검토의 HTTP cursor 복구, 새로고침 후 상태 유지, 모바일 가로 폭과 로그아웃을 확인한다. WebSocket 메시지는 실제 서버에 연결해 전달하며 테스트는 해당 브라우저 연결만 중단한다. 성공 시 자신의 시험 카드만 `data/screenshots/phase6-event-mobile.png`에 저장한다. 당시 전체 Playwright 테스트 7건의 수집만 통과했으며 실제 화면 검증은 권한 변경 후 완료했다. E2E 파일만 별도로 `tsc` 검사할 때 설치되지 않은 `node:fs` 타입 선언 때문에 실패한 이전 진단은 Playwright 실행/애플리케이션 타입 검사와 구분한다.

HTTP/WS 논리 테스트는 socket을 열지 않는 ASGI와 SQLite를 사용한다. sandbox의 교차 스레드 wakeup 제한 때문에 **테스트 안에서만** blocking dispatch를 직접 실행한다. DB writer concurrency는 실제 Python thread로 검사했다. 이를 실제 MariaDB 행 잠금/동시 commit, native ASGI backpressure, GPU 처리량 검증으로 해석하지 않는다. AI 모델/가중치/CUDA 설정/의존성 버전은 변경하지 않았다. 기존 이용 조건은 [models.md](models.md)를 따른다.

실행한 주요 명령:

```bash
.venv/bin/ruff check backend scripts
.venv/bin/python -m pytest backend/tests/test_events.py backend/tests/test_phase6_smoke.py backend/tests/test_foundation.py::test_invalid_configuration_hides_secret_values backend/tests/test_faces.py::test_alignment_known_rotation_scale_translation_and_embedding_validation backend/tests/test_faces.py::test_calibration_rejects_split_leakage_bad_intervals_and_false_pair_labels backend/tests/test_persons.py::test_invalid_vectors_and_deterministic_ties -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
cd frontend
node tests/event-feed.test.mjs
node tests/face-cropper.test.mjs
node tests/face-save.test.mjs
```

## 권한 변경 후 전체 회귀 및 브라우저 검증 완료 (2026-10-03)

DB·localhost·asyncio wakeup·Chrome 접근이 정상인 환경에서 실제 DB 통합 1건과 전체 backend 91건을 통과했다. MariaDB에서 `0005_match_events` head를 직접 확인했으며 이미 적용되어 있어 추가 migration이나 서비스 재시작은 필요하지 않았다. frontend 논리 테스트 12건, 애플리케이션 TypeScript 검사와 production build(529.81 kB)도 통과했다.

첫 Chrome 실행에서는 5건이 통과하고 시험 코드 2건이 실패했다. Chrome DevTools가 JPEG Blob 요청 본문을 생략하여 얼굴 크롭 시험의 `postDataBuffer()`가 null이었다. 실제 XHR에 전달하는 동일 Blob을 변경 없이 디코딩하도록 시험을 수정하여 180×240 크롭 전송과 서버의 112×112 등록 사진을 확인했다. 이벤트 시험은 Playwright WebSocket route의 서버 쪽만 닫아 브라우저에 종료가 전달되지 않았다. 브라우저 쪽과 서버 쪽을 명시적으로 닫도록 수정했다. 제품 코드와 모델 설정은 변경하지 않았다.

수정 후 Chrome 전체 7건을 재시도 없이 통과했다. 얼굴 저장·취소·재등록, 기존 로그인/카메라/MP4 GPU 분석/얼굴 품질/인물 비교, RTSP 중단·자동 복구·중지와 신규 이벤트 검증을 완료했다. 이벤트는 실제 CUDA 후보와 실제 인증된 WebSocket을 사용하며 확인·거부, 보호된 사진, 연결 중단 중 검토 저장 후 HTTP 변경 cursor 복구, 새로고침 후 상태 유지, 390 px 모바일 표시와 로그아웃을 확인했다. 자신의 모바일 이벤트 카드 캡처도 직접 검토했다.

시험 전후 카메라·인물·얼굴·계정의 기존 ID 목록과 카메라 상태가 일치했다. 최종 카메라 2개·인물 3명·얼굴 2개·계정 1개이며 추가 시험 행은 남아 있지 않다. backend·worker·Qdrant·frontend 서비스는 모두 active이고 API health는 정상이다. 시험 자료의 이벤트/사진 삭제는 기존 정기 cleanup을 따른다.

최종 결과는 `data/reports/phase6-regression.json`(`status=passed`), backend 및 DB JUnit 결과는 `phase6-backend-regression.xml`, `phase6-integration.xml`, 브라우저 결과는 `phase6-e2e.json`에 저장했다. 첫 브라우저 실행 결과는 `phase6-e2e-initial.json`에 보존했다. 보고서는 비밀값을 출력하지 않으며 실행 로그/브라우저 원본 보고서는 파일 권한 0600으로 보관한다.

## 반복 실행 방법 및 후속 범위

전체 회귀는 프로젝트 root에서 다음 순서로 반복할 수 있다. DB 통합 테스트는 `0005_match_events` head와 추가 테이블, 로그인/카메라 CRUD 및 Qdrant 연결도 확인한다. GPU 시험과 다른 benchmark를 동시에 실행하지 않는다.

```bash
CCTV_RUN_INTEGRATION=1 .venv/bin/python -m pytest -m integration -q
.venv/bin/python -m pytest -m 'not integration' -q
npm --prefix frontend run test:e2e
```

native smoke는 공개 person_b_reference.jpg/face-smoke.mp4와 자신의 임시 인물/카메라만 사용한다. CUDA 후보→DB/JPEG→WebSocket, 검토/오프라인 변경 복구, 카메라 분석 재시작 후 새 세션 이벤트를 검사한다. 자신이 만든 인물/카메라만 삭제 요청하며 이벤트 사진은 정기 cleanup에서 제거한다. 결과는 credential/embedding 없이 data/reports/phase6-native.json에 기록한다. 전체 회귀는 완료했으며 다음 개발 범위는 Phase 7이다. 정확도 보정과 실제 처리량/장시간 부하 측정은 이번 기능 회귀의 범위에 포함하지 않는다.

주요 파일은 models/events.py, services/events.py 및 event_stream.py, api/events.py, worker/events.py, migration 0005_match_events.py, worker 얼굴/frame/lifespan 경로, event-panel.component.ts, 테스트와 문서다. 기본 API 동작 참고: [Starlette WebSocket](https://starlette.dev/websockets/), [websockets sync client](https://websockets.readthedocs.io/en/stable/reference/sync/client.html).
