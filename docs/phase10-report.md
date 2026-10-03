# Phase 10 비교점수 기준 평가 도구

`scripts/calibrate_face_threshold.py`는 사람이 표시한 사진 쌍 및 MP4 출현 구간으로 0.65/0.70/0.75/0.80/0.85 기준을 평가한다. 얼굴 쌍의 TP/FP/FN/TN·precision/recall/FPR/FNR/F1·ROC/AUC, 품질 통과 표본과 품질 제외를 누락으로 포함한 시도 결과를 분리한다. 1:N은 인물별 최대 reference 점수를 사용하고 미등록 인물의 오탐, 등록 인물의 누락·잘못된 identity를 별도로 기록한다.

MP4의 출현 구간 평가는 실제 YOLO·ByteTrack·SCRFD·ArcFace·품질 gate·최근 샘플 window·품질 가중 평균·2장 이상의 기준 지지 로직을 사용한다. inference는 기준마다 반복하지 않고 비교 상태만 분리한다. 사람 표기 얼굴 영역·출현 구간에 유일하게 대응하는 검출을 사용한다. 영역이 없거나 gallery를 명시하지 않으면 이 평가는 unavailable이다. 영상 출현 구간 결과는 평가 대상으로 표기된 구간에 대한 결과이며 모든 장면의 무제한 오탐률로 해석하지 않는다. 얼굴 쌍 ROC와 구간별 consensus 판정은 별도 단위다.

원본 SHA256·크기·영상 길이를 검증하고 프로젝트 내부의 private 파일만 읽는다. 촬영 source_group이 calibration/evaluation에 섞이면 거부하고 같은 촬영의 등록 사진과 probe로 실제 threshold를 평가하는 것도 거부한다. evaluation probe를 gallery에 등록할 수 없다. 기준 제안은 calibration의 F1만 사용하며 evaluation에는 선택된 고정값을 적용한다. evaluation의 정답으로 기준을 재선택하지 않는다. 제안은 얼굴 쌍 기준이고 실제 영상 consensus 결과를 함께 검토해야 한다. DB 기준은 자동 변경하지 않는다.

현재 공개 자료는 같은 사진과 밝기 변형·반복 영상이므로 purpose=pipeline_smoke, accuracy_calibrated=false다. 새 manifest는 현재 파일 hash/길이를 사용하고 이전 Phase 3 manifest는 그대로 보존한다. 독립 정확도 자료가 없어 실제 기준 제안과 정확도 보정은 unavailable이며 운영 기준 0.7을 유지했다. 일부 얼굴 사진은 실제 품질 gate에서 제외되었고 score를 임의로 넣지 않았다. 표본 수와 제외 사유를 모두 기록한다.

confirmed/rejected 이벤트는 `--feedback-only`에서 운영자 검토 건수와 확인 비율로 요약한다. 후보 선택 과정에서 놓친 양성/비후보 음성이 없어 recall/FPR/FNR/ROC를 제공하거나 보정 자료로 대체하지 않는다.

실행:

```bash
.venv/bin/python scripts/prepare_calibration_smoke.py
.venv/bin/python scripts/calibrate_face_threshold.py --dataset data/calibration/phase10-smoke.json --pipeline --gallery-reference person_b_reference --output data/reports/phase10-native-smoke.json
.venv/bin/python scripts/calibrate_face_threshold.py --feedback-only --output data/reports/phase10-feedback.json
# 실제 독립 자료: source_group을 촬영 단위로 나누고 gallery ID를 명시한다.
.venv/bin/python scripts/calibrate_face_threshold.py --dataset data/calibration/independent.json --pipeline --gallery-reference enrollment_1 enrollment_2
```

native 평가는 실행 중인 다른 카메라가 있으면 중단하며 설정은 DB의 현재 값을 읽어 사용한다. 모델 버전·SHA·실제 CUDA·품질·sampling·gallery 합산·판정 단위·원본 manifest SHA가 private 결과 JSON에 함께 기록된다. 파일에는 사진이나 embedding을 저장하지 않는다. 기존 인물/카메라/이벤트/gallery를 변경하지 않고 보고서만 만든다. 독립 자료의 수치가 없는 상태를 성공한 실제 정확도 보정으로 보고하지 않는다.

주요 변경 파일은 services/calibration.py, schemas/calibration.py, scripts/calibrate_face_threshold.py·prepare_calibration_smoke.py 및 지표/분리/무결성 테스트다. 신규 모델·의존성·DB migration은 없으며 head 0007_event_clips를 유지한다. 공개 smoke에서 SCRFD/ArcFace와 YOLO의 실제 CUDA 실행, 양성 얼굴 비교, 등록/미등록 두 출현 구간 및 다섯 기준의 평가 경로를 확인했다.

전체 backend 131건과 실제 MariaDB 통합 1건, Ruff를 통과했다. 기준 경계·거절 표본·0인 분모·완벽/역순/동점 ROC·calibration만 이용한 선택·미등록 오탐·등록 누락·촬영 유출·원본 변조·미검출 구간의 누락 분모 포함을 검사했다. backend 서비스에 Phase 10 상태를 반영하고 현재 기능 설정 15 FPS/0.2초/ROI 4/기준 0.7 유지도 확인했다. UI와 worker 분석 코드는 이 Phase에서 변경하지 않아 Phase 9에서 통과한 화면·클립·RTSP 검증을 유지한다. native 결과는 data/reports/phase10-native-smoke.json, feedback/no-truth의 unavailable 결과는 phase10-feedback.json·phase10-no-truth.json이다. 전체 장면의 annotation 완전성이 선언되지 않아 whole_scene_precision은 unavailable이며 영역 밖·모호한 후보 수를 별도로 제공한다.
