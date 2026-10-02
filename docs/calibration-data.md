# Calibration ground truth

Phase 3에서는 정답 파일 형식과 로컬 smoke 자료를 준비했다. 검색 threshold의 보정과 FAR/FRR/precision/recall 측정은 아직 수행하지 않았다. 반복한 공개 사진으로 만든 MP4는 처리 경로와 성능 확인에만 사용한다.

## 형식

`backend/app/schemas/calibration.py`의 `CalibrationDataset`이 JSON을 검증한다. `scripts/prepare_face_samples.py`는 `data/calibration/schema.json`과 `phase3-smoke.json`을 생성한다. 원본/파생 이미지와 동영상은 Git 및 public static 경로에 포함하지 않는다.

| 필드 | 의미 |
| --- | --- |
| `schema_version` | 현재 1 |
| `purpose` | `pipeline_smoke` 또는 `threshold_evaluation` |
| `provenance` | 수집·사용 조건과 정답 작성 방법 |
| `sources` | 상대 경로, SHA256, 해상도, 동영상 길이, source ID 및 촬영 group/split |
| `appearances` | source/익명 subject ID, 출현 구간 `[start_seconds,end_seconds)`, 수동 face region, gallery 등록 여부, 정답 작성자 |
| `references` | reference ID, source/subject ID와 원본 좌표계 crop `[x1,y1,x2,y2]` |
| `trials` | 두 reference ID와 사람 정답에 기반한 `same_subject` |

`source_group`은 같은 사진/촬영 세션과 그 파생 영상·crop·밝기 변형을 묶는다. 같은 group은 `smoke`, `calibration`, `evaluation` 중 하나에만 속할 수 있다. 정답 box는 detector가 만든 값으로 대체하지 않는다. 다른 subject의 negative pair를 positive로 표시하거나, 출현 시간을 영상 길이 밖으로 지정하면 검증을 거부한다. bbox는 해당 source 해상도 안에 있어야 한다.

## 준비한 자료의 범위

[Ultralytics 공개 zidane.jpg](https://github.com/ultralytics/assets/blob/main/im/zidane.jpg)를 직접 확인하여 왼쪽/오른쪽 두 사람을 `person_a`/`person_b`로 익명 표기했다. 1280×720, 10 FPS, 40초 반복 정지 이미지 MP4와 각 사람의 reference/밝기 변형 총 4개를 준비했다. 사람이 표시한 출현 구간과 넓은 얼굴 영역은 reference 예제이며 정확한 landmark annotation이 아니다. 예제 gallery는 `person_b`, 미등록 subject는 `person_a`로 표시한다. Phase 3 worker는 gallery를 읽거나 동일인을 판정하지 않는다.

positive pair 1개와 negative pair 1개를 제공하지만 독립 표본이 아니며 정확도 평가에 사용할 수 없다. 얼굴 크기/흐림/밝기/자세 gate의 동작은 단위 테스트와 실제 GPU 공개 이미지 시험으로 별도 검증한다. 품질에 미달한 probe가 생기면 유사도 점수를 꾸며 넣지 않고 `quality_rejected`로 집계해야 한다.

## 후속 평가 자료 수집

시험 목적에 맞게 사용할 수 있는 촬영 자료를 서로 다른 세션/카메라/조명/각도로 수집한다. 등록 인물의 복수 reference, 같은 인물의 별도 촬영 probe, 미등록 인물과 threshold 아래 사례를 포함한다. 촬영 group 단위로 calibration/evaluation을 나누고 한 번 고정한 threshold는 독립 evaluation에 그대로 적용한다.

평가 artifact에는 모델 SHA/version, gallery 목록과 person별 점수 집계 규칙, quality 설정, frame sampling, threshold, 판정 단위(얼굴 쌍/track/출현 구간), ground truth 파일 SHA256을 기록한다. detection/quality 단계의 누락과 embedding 비교의 FAR/FRR을 분리한다. 근거가 없는 지표는 `unavailable`로 보고한다. 운영자 confirmed/rejected event를 독립 정답으로 대신 사용하지 않는다.
