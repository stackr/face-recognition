# 비교점수 기준 설정 (2026-10-03)

기능 설정에 **비교점수 기준** 입력과 현재 적용값 표시를 추가했다. 코사인 유사도 범위 -1~1에서 입력하며 기본은 0.75다. 낮추면 후보가 늘고 높이면 더 엄격하게 후보를 선택한다. 관리자만 저장할 수 있으며 기존 CSRF·revision 충돌 검사·반영 대기/자동 재시도 흐름을 사용한다.

`GET /api/function-settings`의 저장값/현재 적용값에 `face_match_threshold`를 포함한다. `PUT /api/function-settings`는 네 설정 항목과 현재 revision을 함께 받는다. 값 누락·범위 초과·NaN/null은 422로 차단한다. 기존 DB JSON에 항목이 없으면 환경의 `FACE_MATCH_THRESHOLD`를 병합하여 이전 FPS·검사 간격·검사 인원을 보존한다. 조회만으로 저장값을 바꾸지 않으며 이후 저장 시 네 항목을 보관한다. 추가 migration 없이 `0006_recognition_controls`를 유지한다.

worker는 프레임 사이에서 저장값을 적용하고 사진 시험 비교에서도 같은 기준을 반환한다. 실시간 비교 cache 키에 기준을 포함하여 이미 보관한 사진의 점수를 즉시 다시 판정하며 불필요한 특징 생성/검색을 반복하지 않는다. 품질 가중 평균과 최소 두 장의 기준 이상 결과 규칙은 유지한다. 음수 기준에서도 검색 결과가 없는 사진을 지지 표본으로 계산하지 않는다.

이벤트 저장 thread는 DB transaction 안에서 현재 저장 기준을 확인한다. 낮아진 기준은 이전 환경 기본값 아래의 유효 후보도 허용하며, 더 엄격한 기준으로 변경하기 전에 queue에 들어온 미달 후보는 저장하지 않고 정상 제외한다. 기존 이벤트/검토 상태/삭제 정보와 세션·추적 키는 유지한다. 검사 로그 상세의 기준은 해당 검사 당시 적용값이다.

검증 결과:

- 전체 backend 116건과 실제 MariaDB 통합 1건 통과.
- frontend 논리 19건, 타입 검사, Angular production build(578.15 kB), Ruff 통과.
- 관련 Chrome 3건 통과: 기능 설정/비교 기준/로그, 이벤트 삭제/재연결, 인물 등록/사진 비교.
- 기존 세 항목만 저장된 DB 설정의 보완과 환경 기본값/DB 저장값 우선순위, 범위/NaN/null·권한/CSRF·충돌 검사를 확인했다.
- 같은 보관 사진에서 0.75→0.90→0.60 변경 시 추가 embedding/검색 없이 후보를 다시 판정하는 것을 확인했다.
- 실제 CUDA MP4에서 0.70→1.00 변경 후 후보 제외, 0.70 복원 후 후보 재표시, 같은 stream session 유지와 이전 이벤트 보존을 확인했다.
- 실제 사진 시험 비교에서도 1.00의 기준 미달과 0.70의 후보 표시, 새로고침 후 값 유지, 모바일 390 px 표시와 로그를 확인했다.
- Chrome 첫 시도에서 Blob 사진 요청의 응답 본문을 DevTools로 읽는 테스트가 실패했다. 요청/서버 결과를 변경하지 않고 Angular 화면에 표시된 응답을 확인하도록 수정한 뒤 관련 3건을 다시 통과했다.

실행 명령:

```bash
.venv/bin/pytest backend/tests -m 'not integration' -q
CCTV_RUN_INTEGRATION=1 .venv/bin/pytest backend/tests/test_mariadb_integration.py -q
cd frontend
node --test tests/*.test.mjs
npm run typecheck
npm run build
PLAYWRIGHT_JSON_OUTPUT_FILE=../data/reports/match-threshold-browser.json npx playwright test recognition-controls.spec.ts event-deletion.spec.ts phase4.spec.ts --reporter=list,json
```

서비스 반영을 위해 backend/worker를 재시작하고 기존 실행 카메라를 복원하는 스크립트를 실행했다. 당시 실행 중인 카메라는 없어 `resumed_cameras=[]`였다. 테스트는 전용 임시 인물/카메라/공개 시험 자료만 생성하고 정리했다. 설정 복원은 테스트가 마지막으로 저장한 revision에만 수행하여 사용자의 새로운 수정이 우선한다. 최종 결과는 `data/reports/match-threshold-regression.json`, 브라우저 결과는 `data/reports/match-threshold-browser.json`, 모바일 화면은 `data/screenshots/match-threshold-settings-mobile.png`에 보관한다. 전체 Chrome 10건을 재실행한 결과는 아니다. 비상업 시험 목적과 모델/가중치/의존성/라이선스를 유지하며 독립 정확도 보정 결과를 의미하지 않는다.
