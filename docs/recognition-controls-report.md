# 얼굴 검사 로그·기능 설정·움직임 대응 개선 (2026-10-03)

사용자 요청에 따라 Phase 7 화면에 **로그**와 **기능 설정** 메뉴를 추가하고, 머리 영역 우선 검출과 여러 프레임 비교를 native 서비스에 적용했다. 인물 관리는 마지막 메뉴에 유지한다. 기존 카메라·인물·등록 사진과 이벤트 인증·중복 방지·재연결 복구를 유지한다. 비상업 시험 목적이며 모델/가중치/외부 의존성은 변경하지 않았다.

## 화면과 API

| 기능 | 경로 / 동작 |
| --- | --- |
| 로그 화면 | `#/logs`, 카메라·결과·주요 사유·기간 필터, 집계, 상세 지표, 50건씩 cursor 조회 |
| 로그 API | `GET /api/recognition-logs`, 로그인 및 카메라 조회 grant 검사 |
| 기능 설정 화면 | `#/settings`, 저장값·적용값·반영 상태 및 검사 빈도 입력 |
| 설정 조회 | `GET /api/function-settings`, 로그인 |
| 설정 저장 | `PUT /api/function-settings`, 관리자·CSRF·revision 충돌 검사 |

로그는 실제 검사한 프레임만 기록한다. 검출 실패, 여러 얼굴, 품질 제외, 사진 수집, 등록 얼굴 없음, 평균 점수 미달, 프레임 간 비교 불일치, 후보 및 검색 서비스 장애를 구분한다. 모든 품질 제외 사유를 보관하고 집계/사유 필터는 첫 번째 사유를 사용한다. 품질 제외 프레임에는 이전 사진의 비교 점수를 붙이지 않는다. 사진·embedding·인물 이름·RTSP 접속 정보는 로그에 포함하지 않는다.

카메라·세션·추적·프레임 조합은 UNIQUE이며 저장 재시도 시 중복을 건너뛴다. 별도 writer의 큐는 기본 256건이고 최대 64건씩 저장한다. DB 실패는 세 번 재시도하며 큐 포화/저장 실패는 worker의 `recognition_logs` 통계에 표시한다. 로그 보관은 기본 7일/100,000건이다. 카메라 삭제 시 해당 진단 기록도 삭제한다. 일반 계정은 사용 중인 카메라의 조회 grant가 필요하고 권한 변경은 다음 조회에 반영된다.

## 실행 중 설정 반영

| 설정 | 범위 | 최초 기본값 |
| --- | --- | --- |
| 사람 검출 빈도 | 1~15 FPS | 5 FPS |
| 인물별 얼굴 검사 간격 | 0.2~10초 | 0.5초 |
| 프레임당 최대 얼굴 검사 인원 | 1~16명 | 4명 |

DB의 `function_settings` singleton에 revision과 값을 저장한다. 최초에는 `.env`를 사용하고 화면에서 저장한 값이 이후 우선한다. worker는 시작 시 저장값을 읽고 2초마다 변경을 확인한다. API는 내부 인증 명령으로 즉시 적용을 요청한다. scheduler가 프레임 사이에서 설정과 ByteTrack의 FPS/분실 프레임 상한을 변경하며 기존 세션·추적 ID·얼굴·이벤트 중복 방지 키를 유지한다.

저장 후 반영 확인이 지연되면 API는 `202`와 미반영 상태를 반환한다. 화면은 실제 적용값을 별도로 표시하고 반영될 때까지 확인한다. 다른 화면이 먼저 저장했으면 `409`로 기존 편집 내용의 덮어쓰기를 막는다. NaN/무한대·범위 밖 값·정수가 아닌 검사 인원·추가 필드는 거절한다.

## 얼굴 검출과 비교

- 여유 영역을 포함한 사람 위쪽 절반을 320px 입력으로 먼저 검사한다. 미검출·작은/잘린 얼굴·특징점 오류에는 전체 사람 영역으로 검사하고 필요한 경우 640px로 재검사한다. 총 세 패스 안에서 처리하며 이웃 얼굴·여러 얼굴 연결 제한을 유지한다.
- SCRFD의 resize, anchor grid와 ONNX shape annotation을 320/640 입력에 맞췄다. 모델 시작 시 두 크기를 실제 추론하고 640 출력 개수를 검사한다. 기존 CUDA convolution 검증을 유지한다.
- 품질을 통과한 사진은 이전 best보다 점수가 낮아도 embedding을 만든다. 최근 최대 5장을 유지하고 사진별 인물 최대 reference 점수를 품질에 따라 평균한다. 평균이 기존 기준 0.75 이상이며 최소 2장도 각각 기준 이상일 때 후보로 표시한다.
- 사용 시간은 기본 3초이며 긴 검사 간격에서는 두 장을 확보하도록 간격×2.5까지 늘린다. 사진·후보 evidence는 만료 시 해제하고 세션 교체·추적 만료에서 버퍼를 비운다. 모든 기존 사진과 cosine 0.3 미만인 큰 특징 변화도 버퍼를 초기화한다. 이 기준은 시험 설정이다.
- 대표 썸네일은 최근 사진 중 최고 품질이다. 이벤트 사진·프레임·시각·품질은 해당 후보의 기준을 충족한 사진 중 최고 품질로 연결한다. 기존 event UNIQUE, cooldown, 권한·검토와 WebSocket/HTTP 복구를 유지한다.

## 검증

| 검증 | 결과 |
| --- | --- |
| 전체 backend + 실제 MariaDB 통합 | **111건 통과**: 독립 110건, 통합 1건 |
| 신규 backend 기능 검증 | 14건: 설정 권한/검증/충돌/반영 대기, 로그 권한/필터/집계/cursor/상한/중복, 머리 ROI/640 좌표/이웃 제외, 낮은 품질의 후속 사진/합의/evidence/만료/긴 간격 |
| 전체 Chrome E2E | **9건 통과**: 기존 8건과 신규 로그/설정/native 다중 프레임 1건 |
| 최종 기능 재검증 | backend 14건과 신규 Chrome 1건 통과 |
| native Phase 6 회귀 | 실제 CUDA 후보·인증 WebSocket·private 이미지·중복 방지·검토 저장·변경 복구·검토 WS 갱신·새 세션 이벤트의 8개 항목 통과 |
| frontend 논리 회귀 | 16건 통과 |
| 타입 검사 / production build / Ruff | 통과, 초기 bundle 573.25 kB |
| 실제 schema | `0006_recognition_controls`, 새 테이블과 singleton 확인 |

신규 Chrome 시험은 공개 사진 기반 MP4와 임시 인물을 사용했다. 실행 중 8 FPS/0.2초/6명으로 변경한 후 적용값과 같은 카메라 세션, 실제 CUDA 처리, 2장 이상 지지하는 후보, 저장 로그의 필터/상세·새로고침 및 390px 모바일 가로 넘침을 확인했다. 시험 설정은 원래 5 FPS/0.5초/4명으로 복원하고 자신이 만든 카메라·인물·사진을 정리했다. 기존 카메라 3개·인물 3명·얼굴 4개·계정 1개의 ID를 보존했다. 최종 확인에서는 별도로 변경된 15 FPS/0.2초/4명과 기존 카메라 한 대의 실행 상태를 확인하여 그대로 유지했다. backend·worker·frontend·Qdrant는 모두 active이고 현재 DB 저장값과 worker 적용값도 일치한다.

스크린샷은 `data/screenshots/recognition-settings-desktop.png`, `recognition-logs-desktop.png`, `recognition-logs-mobile.png`다. 종합 실행 기록은 `data/reports/recognition-controls-regression.json`이다.

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/python scripts/restart_analysis.py
CCTV_RUN_INTEGRATION=1 .venv/bin/python -m pytest -q
npm --prefix frontend run test:e2e
node --test frontend/tests/*.test.mjs
npm --prefix frontend run build
.venv/bin/python scripts/check_phase6.py
```

이번 검증은 처리 경로와 회귀 확인이다. 반복 정지영상의 기능 시험으로 움직이는 사람의 인식 성공률·오인식률 개선을 수치로 보장하지 않는다. 실제 움직임·조명·얼굴 각도와 미등록 인물이 포함된 독립 영상 평가, GPU 처리량에 따른 빈도 조정이 후속 검증 대상이다.
