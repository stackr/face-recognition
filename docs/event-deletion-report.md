# Live Search 검색 이벤트 삭제 (2026-10-03)

오른쪽 검색 이벤트에 개별 **삭제**와 현재 필터에 표시된 삭제 가능 이벤트의 **목록 삭제**를 추가하고 서비스에 반영했다. 확인창에서 대상/건수를 확인하며 취소하면 기록과 사진을 유지한다. 삭제 대기 중에는 삭제/검토를 중복 실행하지 않는다. 목록 삭제는 현재 표시된 최대 100건에 한정하며 이후 과거 보관 기록이나 새 검출이 표시될 수 있다.

서버는 로그인·CSRF·카메라 운영 grant·인물 조회 grant를 확인한다. admin과 운영 권한이 있는 operator만 삭제 가능하며 viewer는 조회만 가능하다. 선택 대상 전체를 먼저 검사해 권한 실패 시 일부 삭제를 방지한다. 중복 ID와 이미 삭제된 ID의 재요청은 idempotent하게 처리한다.

삭제 상태와 변경 journal/감사 기록을 같은 transaction에 저장하고 검출 얼굴·프레임 파일을 제거한다. 목록/상세/사진/검토에서는 즉시 차단한다. 등록 인물과 등록 얼굴은 삭제하지 않는다. 중복 방지와 오프라인 변경 복구에 필요한 이벤트 행은 기존 30일 보관 만료까지 유지하며, 같은 세션·추적·인물의 더 나은 검출이 다시 등록되지 않는다. 새로운 세션/추적은 별도 이벤트다. 파일 삭제 실패/commit 응답 유실은 기존 SQL 기준 orphan 정리로 복구한다.

HTTP 변경 cursor와 인증된 WebSocket이 `event_deleted`를 전달하며 새 권한도 검사한다. Angular는 삭제 표식과 snapshot revision을 사용해 늦게 도착한 증거가 삭제를 되돌리지 않게 한다. 페이지 종료 후 진행 중 구독을 종료한다. 기존 status 컬럼을 사용하므로 migration head는 `0006_recognition_controls`를 유지한다. backend만 재시작했으며 worker 재시작·설정 변경은 수행하지 않았다.

검증 결과:

- 전체 backend 112건 통과. 실제 MariaDB 통합 1건 통과.
- frontend 논리 19건 통과. 타입 검사·Angular production build(576.73 kB)·Ruff 통과.
- 관련 Chrome E2E 3건 통과: 신규 삭제 검사, 기존 Phase 6 이벤트 검토/재연결 검사, 기존 Phase 7 화면/필터/URL/모바일 검사.
- 신규 실제 CUDA 시험에서 전용 임시 인물/카메라와 공개 MP4로 서로 다른 세션의 이벤트 3건을 생성하고 개별 삭제 취소·삭제, 사진/프레임 404, 필터 목록 삭제와 다른 상태 유지, 다른 브라우저 화면의 실시간 삭제, WebSocket 중단 중 삭제의 HTTP cursor 복구, 새로고침 후 미표시를 확인했다.
- 단위 테스트에서 실제 파일 제거, 동일 추적 재생성 방지, 새 세션 이벤트 허용, 선택 전체 권한 검사와 rollback, 운영 grant/인물 grant 변경, viewer/CSRF 차단, 삭제 요청 형식 제한, 삭제 journal 페이지와 보관 만료 정리를 확인했다.
- 모바일 390 px에서 버튼·사진·가로 넘침을 확인했다. `data/screenshots/event-deletion-mobile.png`에 화면을 저장했다.

실행 명령:

```bash
.venv/bin/pytest backend/tests -m 'not integration' -q
CCTV_RUN_INTEGRATION=1 .venv/bin/pytest backend/tests/test_mariadb_integration.py -q
cd frontend
node --test tests/*.test.mjs
npm run typecheck
npm run build
PLAYWRIGHT_JSON_OUTPUT_FILE=../data/reports/event-deletion-browser.json npx playwright test event-deletion.spec.ts phase6.spec.ts phase7.spec.ts --reporter=list,json
```

실제 브라우저 검사는 자신이 생성한 임시 자료만 정리하며 기존 데이터/설정/카메라 상태를 보존한다. 이번 검사에서는 전체 Chrome 10건을 다시 실행하지 않았으며 얼굴 검사 로그·설정의 기존 전체 9건 통과 기록은 이전 개발 결과다. 최종 결과는 `data/reports/event-deletion-regression.json`, 브라우저 원본 결과는 `data/reports/event-deletion-browser.json`에 저장한다. 비상업 시험 목적과 모델·가중치·의존성·라이선스를 유지한다.
