# 얼굴 사진 크롭 (2026-10-03)

인물 등록·수정 페이지에서 로컬 JPEG/PNG를 선택하면 저장 전 크롭 화면을 표시한다. 사진에서 드래그해 영역을 지정하고, 영역 안에서 이동하거나 오른쪽 아래 손잡이로 크기를 바꾼다. 시작 X/Y와 가로/세로 픽셀 입력도 지원한다. 처음에는 전체 사진을 선택하며 **전체 사진 선택**으로 초기화할 수 있다.

미리보기를 확인하고 **크롭한 얼굴 저장**을 눌러 선택 영역을 JPEG로 전송한다. 서버의 기존 얼굴 탐지·품질 검사·정렬·특징 생성·인증·보관 정책을 적용하고 최종 112×112 얼굴만 보관한다. 원본 파일/EXIF는 전송하지 않는다. 사진은 기존 10 MB/4096 px/1200만 픽셀 제한을 유지하며 크롭 영역은 가로·세로 최소 80 px다. 선택만으로는 서버에 등록하지 않는다.

새 인물은 이름을 입력하면 얼굴 저장 시 인물을 먼저 생성하고 사진을 연결한다. 얼굴 검사/저장 실패 시 해당 인물과 크롭을 유지하여 사진만 재시도한다. 기존 인물의 다른 정보 수정은 **인물 저장**으로 처리한다. 여러 사진은 최대 20장까지 한 장씩 크롭·저장·취소한다. 취소/페이지 이동 시 로컬 미리보기 URL을 해제한다. 신규 의존성, API 또는 DB schema 변경은 없다.

## 검증

- TypeScript 검사와 Angular production build 통과.
- 좌표 테스트 3개: 화면 크기와 원본 좌표 변환, 영역 이동/크기 조절의 경계 및 최소 크기, 원래 해상도의 선택 영역 출력.
- 저장 흐름 테스트 2개: 신규 인물 생성 후 사진 실패·재시도 시 중복 생성 방지, 이름 없음/조회되지 않은 기존 인물의 등록 차단.
- 기존 인물 E2E를 명시적 크롭 저장에 맞춰 갱신하고 신규 크롭 E2E를 추가했다. 새 시험은 공개 사진의 지정 영역만 전송되는지, 저장 전 서버 쓰기가 없는지, 인물 자동 생성·정렬 사진·수정 페이지 취소/재등록 및 모바일 가로 넘침을 검사한다.
- Chrome 실행이 sandbox의 `setsockopt: Operation not permitted`로 종료되어 실제 브라우저와 CUDA 저장 검증은 이번 변경에서 실행하지 못했다. E2E 6개가 등록되는 것만 확인했으며 통과로 보고하지 않는다.

```bash
npm --prefix frontend run typecheck
npm --prefix frontend run build
cd frontend
node tests/face-cropper.test.mjs
node tests/face-save.test.mjs
# 로컬 서비스와 브라우저 실행이 가능한 환경에서 실행
npx playwright test
```

주요 파일은 `frontend/src/app/face-cropper.component.ts`, `app.component.ts`, `app.component.html`, `frontend/src/styles.css`, `frontend/tests/face-cropper.test.mjs`, `face-save.test.mjs`, `frontend/e2e/face-crop.spec.ts`, `phase4.spec.ts`다. 자동 얼굴 영역 제안은 제공하지 않으며 사용자가 영역을 지정한다. 얼굴 탐지·품질 검사는 저장 요청 때 서버에서 수행한다.
