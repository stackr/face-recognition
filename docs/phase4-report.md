# Phase 4 검증 결과

2026-10-02, 비상업 시험 환경. 인물/다중 얼굴 등록, 얼굴 유사도 검색, 인물·이미지 접근 제어, 감사 기록, 삭제 재시도 및 보관 기간 정리를 구현하고 native 서비스에 반영했다.

## 구현과 사용

**인물 관리 → 인물 추가 → 모달의 인물 저장 → 얼굴 사진 추가**에서 여러 JPEG/PNG를 등록한다. 첫 화면에는 인물 목록을 표시하고 추가·수정 및 사진 시험 비교는 모달에서 진행한다. 한 사진에 얼굴이 없거나 여러 개면 거절하고 기존 크기·선명도·밝기·자세 품질 기준을 적용한다. 한 인물당 기본 최대 20개, 전체 100명이다. 원본을 저장하지 않고 정렬된 112×112 JPEG만 디렉터리 0700/파일 0600으로 보관한다. 이미지 및 특징 보관 기간은 각각 기본 30일이다.

**사진으로 시험 비교**와 **영상 분석 → 추적별 얼굴 분석**에서 등록 인물 후보를 확인한다. 인물별 점수는 여러 reference 중 최대 cosine similarity이며 시험 임계값은 0.75다. admin은 전체를 관리하고 operator/viewer는 별도 인물 grant가 있어야 정보·이미지·후보 이름을 조회한다. 사진 시험 비교는 admin/operator에게 제공한다. 영상 권한과 인물 권한을 각각 검사한다.

worker는 모델 및 복호화한 reference cache를 소유한다. 사진 추론은 기존 단일 GPU scheduler의 bounded queue에서 실행한다. API에는 raw embedding/저장 경로를 반환하지 않는다. 등록 특징은 MariaDB에서 Fernet 암호화하고 localhost Qdrant의 모델 버전별 512D cosine collection에 저장한다. 기존 RTSP 암호화 키를 공유한다. 다른 owner/model-version의 기존 collection을 덮어쓰지 않는다.

DB revision 및 검색 자격을 기준으로 worker cache를 갱신한다. 기본 auto는 reference 200개 이하에서 Memory, 초과 시 Qdrant를 사용한다. 현재 Qdrant는 모든 유효 reference를 exact 검색 후 인물별 최대 점수로 집계한다. 만료·비활성화·삭제 대상과 권한이 해제된 인물은 오래된 worker 결과에서도 API가 제외한다. DB 검사가 실패하면 확인되지 않은 후보 정보를 반환하지 않는다.

삭제는 desired state/job을 먼저 commit하고 worker ACK, Qdrant, 파일, DB를 순서대로 정리한다. 실패는 durable job/backoff로 재시도하며 202/pending으로 표시한다. 이미지와 특징이 각각 만료되므로 이미지가 먼저 사라져도 유효한 특징 검색은 가능하고, 특징이 먼저 만료되면 검색에서 제외하면서 아직 유효한 이미지를 유지한다. 기본 30초 cleanup, 200 MB 이미지 저장 한도, 500 MB 최소 디스크 여유 공간을 적용한다. 파일 생성 후 commit 실패는 즉시 복구하고 프로세스 종료로 남긴 orphan은 1시간 유예 후 정리한다. 완료 job은 7일 후 정리하며 감사 로그는 유지한다.

## 검증

| 항목 | 결과 |
| --- | --- |
| Python 전체 단위 테스트 | 45 passed |
| 실제 MariaDB 통합 테스트 | 1 passed |
| Alembic schema 비교 | 추가 upgrade 없음, head `0004_person_faces` |
| Chrome E2E | 4 passed: 로그인/카메라, MP4, 얼굴 분석, 인물 등록·검색·삭제 |
| Angular production build | 통과, 초기 번들 511.34 kB |
| TypeScript / Ruff / diff whitespace | 통과 |
| 실제 CUDA 등록·검색·정리 | 통과, 공개 smoke 자료만 사용 |

단위 테스트에는 여러 reference의 인물별 집계 및 Memory/Qdrant 일치, 이미지 권한·감사 기록·raw vector 비노출, 등록 commit 실패 시 파일 제거, Qdrant/worker 장애 시 삭제 재시도, upsert 이후 ACK 실패 시 pending 복귀, 비활성화·삭제 즉시 제외, 이미지/특징별 만료, 만료 cache 제외, 잘못된 이미지·얼굴 수 거절 및 기존 collection 보존을 포함한다.

최종 native 검증은 `scripts/check_phase4.py`로 공개 사진의 얼굴 crop/밝기 변형 2개를 등록했다. 같은 사진 비교는 Memory/Qdrant 모두 1.0, 반복 MP4의 live 후보는 0.974650이었다. 영상 분석 중 사진 비교를 요청해도 카메라 처리 프레임이 계속 증가했다. 인증 없는 이미지 조회 401, 얼굴 0개/여러 개/잘못된 파일 422, 비활성화 직후 후보 제외, 이미지와 특징의 독립 만료 및 DB/vector/file/cache 정리를 확인했다. 임시 인물·얼굴·MP4 카메라는 종료 시 삭제했다. 기존 등록 카메라 2대 및 중지 상태를 유지했다.

상세 native 결과는 private `data/reports/phase4-native.json`, Chrome 화면은 `data/screenshots/phase4-person-modal.png` 및 `data/screenshots/phase4-person-modal-mobile.png`에 있다. 실제 카메라 영상이나 얼굴을 시험 자료로 저장하지 않았다. 테스트 환경의 Starlette/httpx deprecation 및 Qdrant memory 모드 exact 검색 안내는 실패가 아니며 관련 의존성을 임의로 변경하지 않았다.

## 한계와 다음 단계

공개 사진·밝기 변형·반복 MP4는 같은 촬영 자료의 smoke 시험이다. 독립 CCTV 정답 자료를 통한 정확도 보정이 없으며 FAR/FRR은 unavailable다. 0.75는 시험 임계값이고 후보는 동일인 확정이 아니다. 다중 카메라 부하와 대규모 Qdrant 성능은 아직 검증하지 않았다. API는 단일 프로세스이며 인물 변경/정리는 공통 lock으로 직렬화한다.

다음은 Phase 5의 RTSP reconnect/backoff와 stream session lifecycle 보강이다. MatchEvent 저장/WebSocket/이벤트 중복 방지는 Phase 6, 전체 Live Search UI는 Phase 7 범위다.


## 인물 관리 화면 변경 검증

목록 첫 화면과 추가·수정 모달을 확인했다. 신규 저장 뒤 모달 유지 및 기존 정보·등록 얼굴 복원, 모달 안의 다중 업로드·시험 비교·오류 표시·얼굴 삭제, Escape 닫기와 열기 버튼으로의 focus 복귀, 처리 중 닫기 차단, 재열기 시 미저장 입력·비교 결과 초기화를 Chrome E2E로 검증했다. 390×844 모바일 화면에서도 모달이 화면 안에 표시되고 하단 닫기 버튼을 사용할 수 있다. API와 DB schema 변경은 없다.
