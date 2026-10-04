# Phase 7 Angular Live Search UI (2026-10-03)

비상업 시험용 Live Search 화면을 구현하고 native 서비스에 반영했다. 왼쪽 CCTV 목록, 중앙 영상·추적·후보, 오른쪽 실시간 MatchEvent를 함께 표시한다. 기존 인물 등록·크롭, 카메라 관리, 확인·거부 및 WebSocket 재연결 복구를 유지한다. **backend 96건, 실제 DB 통합 1건, Chrome 8건과 frontend 논리 16건을 모두 통과했다.**

## 화면과 동작

- 왼쪽에서 조회 가능한 카메라를 이름/위치로 찾고 선택한다. 선택 상태, 입력 유형, 사용 여부와 조회 전용 여부를 표시한다. 인물 관리 메뉴는 마지막에 유지한다.
- 중앙에서 기존 MP4/RTSP 분석 시작·중지, 인증된 MJPEG, 같은 분석 프레임의 person bbox/track ID, 권한 있는 추적별 유사도 후보를 확인한다. 후보 수와 얼굴 카드 강조를 추가했으며 상세 지표는 펼쳐서 볼 수 있다.
- 오른쪽에서 현재 등록 얼굴과 검출 얼굴, 인물·카메라·추적 번호·시각·유사도·품질·검토 상태 및 확인·거부를 표시한다. 카메라 범위(선택/전체), 인물 이름, 상태 필터와 초기화를 제공한다.
- 큰 화면은 3열, 1200 px 이하는 카메라 목록을 위로 배치하고, 1100 px 이하는 한 열로 표시한다. 390 px 모바일에서 가로 넘침과 사진·검토 버튼을 확인했다.

카메라 선택은 `#/live/{camera_id}`에 저장한다. 직접 주소 진입, 새로고침, 뒤로/앞으로 가기에서 복원하며 다른 탭에서 추가된 카메라도 최신 목록을 조회해 확인한다. 찾을 수 없거나 권한이 없는 카메라는 안내하고 미리보기를 표시하지 않는다. 카메라/페이지 변경 및 로그아웃 후 늦게 도착한 상태·목록 응답은 화면에 적용하지 않는다. 분석 상태는 2초, 카메라/등록 얼굴 목록은 30초마다 갱신한다. 다른 카메라 선택이 기존 카메라 분석의 시작·중지를 뜻하지는 않는다.

이벤트 필터는 권한 있는 최근 100건의 표시만 바꾼다. 전체 이벤트 feed와 HTTP 변경 cursor를 유지하므로 필터 밖에서 발생한 변경도 복구한다. 선택 전에는 전체 권한 범위의 최근 이벤트를 표시한다. 전체 보관 기록의 서버 검색/페이지 이동은 이 화면에 추가하지 않았다.

등록 사진은 현재 조회 가능한 `ready` reference 중 이미지가 있는 가장 높은 품질의 사진을 대표로 표시한다. 동일 품질은 face ID로 정렬한다. 이벤트에 검출 당시 reference ID가 저장되어 있지 않으므로 **현재 등록 얼굴**이라는 이름과 설명으로 구분한다. 등록 사진의 인증된 기존 endpoint를 사용하고, 만료/삭제/권한 변경으로 사진 조회가 실패하면 안내로 교체한다. 실패 URL 목록도 최대 256건으로 제한한다. 원본 파일/embedding을 브라우저에 추가로 반환하지 않는다.

## 권한과 서비스 반영

카메라 응답에 현재 계정의 `can_view`, `can_operate`를 추가했다. admin은 전체, viewer는 조회 grant, operator는 조회/조작 grant를 따른다. Live Search 목록은 `can_view` 카메라만 표시하고 시작·중지는 `can_operate`일 때 제공한다. 카메라 비활성화와 MP4 미등록도 시작 버튼에 반영한다. 이벤트 검토는 기존 `can_review` 및 CSRF/API 권한 검사를 유지한다. 화면의 버튼 표시가 API 권한 검사를 대신하지 않는다.

backend 서비스만 재시작하여 API/health의 Phase 7 반영을 확인했다. worker/GPU 모델은 재시작하거나 변경하지 않았다. 새로운 DB migration/의존성은 없으며 MariaDB head는 `0005_match_events`다. 시험 전후 기존 카메라 2개·인물 3명·얼굴 2개·계정 1개의 ID와 카메라 실행 상태가 일치했고 임시 행은 남아 있지 않다. backend·worker·frontend·Qdrant는 모두 active다.

## 검증

| 검증 | 결과 |
| --- | --- |
| 전체 backend 회귀 | 96건 통과: 기존 91건 + 역할/grant별 capability 5건, 23.64초 |
| 실제 MariaDB 통합 | 1건 통과: migration head, 추가 테이블, 인증/CRUD/Qdrant, 0.52초 |
| frontend 논리 | 16건 통과: 이벤트/필터/사진 선택 11건 + 크롭/저장 5건 |
| TypeScript / Angular build / Ruff / diff | 통과, production bundle raw 545.21 kB |
| Chrome | 기존 7건 + 신규 Live Search 1건, 전체 8건 통과, 실패/건너뜀/재시도 0건, 47.47초 |
| 화면 캡처 | 자신의 데스크톱 배치와 모바일 사진 비교 카드 직접 검토 |

신규 Chrome 시험은 자신의 임시 인물·카메라와 공개 시험 사진/MP4를 사용한다. 실제 CUDA 후보, 영상·후보 수, 두 얼굴 사진(112×112), 3열 위치, 카메라 목록·상태/인물 필터, 카메라 URL·뒤로 가기·새로고침, 모바일·로그아웃을 검사한다. 사진 404와 capability에 따른 UI 표시는 해당 브라우저의 HTTP 응답을 모의하며 실제 SQL 역할/grant 검사는 별도 backend 5건으로 확인한다. 기존 Phase 6 시험은 새 사진 쌍에서 검출 얼굴을 명확히 지정하여 WebSocket 중단 후 HTTP 복구와 확인·거부를 계속 검증한다.

검증 중 오래된 목록에서 새 카메라 URL을 복원하지 못하는 문제를 수정했다. 필터 label과 select를 분리하여 접근성 이름이 옵션 문자열과 합쳐지는 문제도 수정했다. 시험은 처음 로그인한 세션으로 임시 자료를 정리하고 추가 로그인 없이 로그아웃한다. 로그인 속도 제한은 유지한다.

최종 수량/보존 결과는 `data/reports/phase7-regression.json`(`status=passed`), backend/DB 결과는 `phase7-backend-regression.xml`, `phase7-integration.xml`, 브라우저 결과는 `phase7-e2e.json`에 기록했다. 첫 Chrome 결과는 `phase7-e2e-initial.json`에 보존했다. 화면은 `data/screenshots/phase7-layout-desktop.png`, `phase7-comparison-mobile.png`다. 원본 브라우저 보고서/로그는 파일 권한 0600으로 보관한다.

```bash
.venv/bin/ruff check backend scripts
.venv/bin/python -m pytest -m 'not integration' -q
CCTV_RUN_INTEGRATION=1 .venv/bin/python -m pytest -m integration -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
cd frontend
node tests/event-feed.test.mjs
node tests/face-cropper.test.mjs
node tests/face-save.test.mjs
npm run test:e2e
```

GPU 기능 시험은 다른 benchmark와 동시에 실행하지 않는다. 짧은 시간에 E2E를 반복할 때 로그인 제한(동일 IP 5분에 10회)을 고려하며 시험 때문에 제한을 낮추지 않는다. 공개 자료의 pipeline smoke이며 정확도 보정/실제 CCTV 정확도/장시간 부하 검증을 의미하지 않는다. 모델/가중치/이용 조건은 기존 [models.md](models.md)를 따른다. 다음 개발 범위는 Phase 8 이벤트 전후 영상 클립이다.

## 2026-10-04 Live Search 화면 배치 변경

선택한 카메라의 영상 박스(사용자 카메라 이름 `Face`)를 화면 맨 위로 이동해 한 열로 전체 콘텐츠 너비를 사용한다. 영상 비율을 유지하고 높이를 화면 높이의 65%로 제한한다. CCTV 목록, 분석 설정·얼굴 분석, 검색 이벤트는 영상 아래에 배치한다. 카메라를 바꾸면 상단 영상의 제목과 입력도 함께 바뀐다.

추적별 얼굴 분석 목록은 얼굴 사진을 위에, 추적 번호·품질·비교 사진 수·후보 정보를 아래에 담은 카드 격자로 표시한다. 화면 너비에 따라 카드 열 수를 조정하며 390 px 모바일에서는 한 열로 표시한다. 인물 등록 화면의 얼굴 목록 스타일은 유지한다.

검증 중 분석 전이나 중지 후 검출 점수가 비어 있을 때 템플릿이 오류를 내고 후속 화면 갱신을 중단하는 문제를 발견했다. 점수가 없으면 `—`를 표시하도록 수정했다. 기존 Live Search 브라우저 시험에 상단 영상의 너비·위치, 얼굴 카드의 가로 배열과 사진·정보 순서, 모바일 배열 및 Angular 콘솔 오류 검사를 반영했다. 분석 중지 후 얼굴 목록 정리와 이벤트 필터, 카메라 URL 복원·접근 권한 검사도 통과했다.

TypeScript 검사, Angular production build(618.07 kB), frontend 논리 19건, 실제 Chrome의 Live Search·전체 페이지 다크 테마 2건을 통과했다. 실제 CUDA와 공개 시험 영상으로 화면을 검증했으며, 데스크톱 및 모바일 캡처를 직접 확인했다. 프런트엔드 서비스의 자동 재빌드로 반영했고 기존 카메라 3개·인물 2명·기능 설정·카메라 실행 상태를 보존했다. 임시 시험 인물과 카메라는 삭제했다. 이번 변경은 프런트엔드에 한정하며 DB migration이나 모델 변경은 없다.

검증 요약은 `data/reports/live-layout-regression.json`, 화면은 `data/screenshots/phase7-layout-desktop.png`, `phase7-faces-mobile.png`, `phase7-comparison-mobile.png`에 보관한다.

```bash
npm --prefix frontend run typecheck
npm --prefix frontend run build
cd frontend
node --test tests/*.test.mjs
npx playwright test e2e/phase7.spec.ts e2e/dark-theme.spec.ts
```


## 2026-10-04 Live Search 오른쪽 분석 컬럼·가운데 이벤트 배치

상단 영상은 왼쪽·가운데 두 컬럼을 차지하고 오른쪽에는 분석할 카메라, 상세 분석 지표, 추적별 얼굴 분석을 순서대로 배치했다. 영상 아래에는 CCTV 목록을 왼쪽, 검색 이벤트를 가운데에 표시한다. 얼굴 목록의 사진·정보 카드 배열은 유지한다.

1280 px 이하에서는 영상과 오른쪽 분석의 두 컬럼을 사용하고 CCTV 목록·이벤트를 영상 아래에 표시한다. 1100 px 이하에서는 한 컬럼으로 쌓는다. 데스크톱·1280 px·390 px 화면에서 위치와 가로 넘침 여부를 확인했다.

타입 검사·프로덕션 빌드(624.24 kB), 실제 Chrome 다크 테마·Live Search·업로드 MP4 얼굴 검출/검색 이벤트 3건을 통과했다. 중간 화면의 경계 너비를 조정한 후 Live Search 브라우저 시험과 빌드를 다시 통과했다. 시험용 카메라·인물은 시험 종료 시 정리했다. 프런트엔드 실행 서비스에 반영했으며 백엔드·워커 재시작이나 DB 변경은 필요하지 않았다.
