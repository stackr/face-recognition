# Phase 11 선택 Person Re-ID

`PersonReIdentifier.extract_embedding(person_crop)`와 `compare(embedding_a, embedding_b)` 인터페이스, disabled 구현과 OSNet x0.25 어댑터를 추가했다. 얼굴 모델과 별도 namespace이며 얼굴 특징과 몸 특징을 서로 비교하지 않는다. 몸 crop을 RGB 256×128 및 ImageNet mean/std로 처리하고 L2 정규화한 512차원 특징을 반환한다. 현재는 의복/외형의 비교 특징이며 등록 인물 identity를 할당하거나 얼굴 검색 이벤트의 기준을 바꾸지 않는다. 카메라 간 연관·이동 경로·topology·travel time과의 결합은 Phase 12 범위다.

모델은 작성자 공식 저장소의 MSMT17 combineall 학습 OSNet x0.25이다. ImageNet 가중치만으로 Re-ID를 구현했다고 보고하지 않는다. 작성자 [Hugging Face 모델 목록](https://huggingface.co/kaiyangzhou/osnet)의 고정 revision a5c5cc037c24235cda3b21085b93ad77c9616224와 LFS SHA256 cf55163d78fc44c62c82f85ab62d39f10438679b5abe8c698ae08cfa84aa6e18을 검증한다. architecture는 [deep-person-reid](https://github.com/KaiyangZhou/deep-person-reid)의 f8cd150fdf77e8d9e1ed143b7f308c2c609ded50에서 가져온 원본을 보존했고 runtime에서 SHA를 재검사한다. MIT 원문은 third_party/osnet/LICENSE에 포함했다. 원본의 legacy ImageNet 자동 다운로드 함수는 사용하지 않는다. 전체 Torchreid/gdown 패키지를 설치하지 않고 현재 고정 PyTorch/Pillow만 사용한다.

가중치는 약 9.3 MB이며 `scripts/prepare_reid.py`에서 명시적으로 준비한다. worker startup은 네트워크 다운로드를 하지 않는다. `torch.load(weights_only=True)`로 읽고 모든 feature 가중치가 정확히 대응해야 하며 학습용 classifier만 제외한다. CUDA 요청과 실제 parameter device를 검사한다. 모델 누락·손상·선택 기능 inference 실패는 Re-ID unavailable로 표시하고 기존 얼굴 분석을 계속한다.

기본 설정은 REID_ENABLED=false다. 활성화 시 공유 GPU scheduler에서 추적별 최대 2 ROI/프레임·2초 간격으로 검사하고 카메라당 최대 100개 특징만 RAM에 보관한다. stream session 회전·lost track·중지/EOF 시 정리하며 실패한 최신 특징도 폐기한다. 사진/몸 crop/embedding을 DB·disk·API·WebSocket에 저장/반환하지 않는다. API에는 특징 준비 여부·frame 시각·모델 버전만 제공하며 Live Search에는 “외형 특징 준비 · 인물 확인 전”을 표시한다. 분석 중 중지가 발생해 이미 지운 cache가 다시 생성되는 경쟁도 회귀 검사로 막았다.

실행:

```bash
.venv/bin/python scripts/prepare_reid.py
.venv/bin/python scripts/check_reid.py
# 실제 서비스에서 잠시 활성화하여 Chrome 검사하고 원래 설정으로 복원
.venv/bin/python scripts/check_reid_browser.py
```

상시 시험 활성화는 .env에 REID_ENABLED=true를 설정하고 `scripts/restart_analysis.py`로 반영한다. 현재 배포 상태는 기본 disabled이며 사용자 설정과 실행 상태를 보존했다. Chrome 활성화 검사는 특정 worker의 runtime systemd drop-in만 사용하고 finally에서 제거·daemon reload·재기동하여 복원한다. .env 비밀값은 변경하지 않는다. 다른 카메라가 실행 중이면 해당 검사는 시작하지 않는다.

실제 RTX 3070 Ti에서 pinned 가중치·CUDA float32 parameter·정규화된 512차원 특징·자기 비교 및 공개 사진의 밝기 변형/다른 몸 crop 비교를 통과했다. 실제 YOLO·얼굴 ONNX·OSNet을 켠 독립 worker에서 두 추적의 몸 특징과 metadata-only 결과, 중지 후 cache=0을 확인했다. 실제 서비스에서도 잠시 활성화한 뒤 Chrome 표시·API 특징 비노출·중지 정리·기본 disabled와 기능 설정 복원을 확인했다. 정지 공개 사진의 처리 경로 검증이며 실제 카메라 간 Re-ID 정확도/mAP/Rank-1 측정을 뜻하지 않는다.

주요 변경은 core/reid_data.py·config.py, worker/reid.py·runtime.py·api.py·vendor/osnet.py, 준비/검증 scripts, Live Search의 선택 상태 표시 및 관련 테스트다. DB migration과 Python/npm 의존성 추가는 없다. 프로젝트의 비상업 시험 목적과 기존 얼굴 모델의 별도 이용 조건을 유지한다. 새 모델의 작성자 model card는 MIT를 표시하며 architecture의 MIT 고지를 함께 보존한다.

최종 검증: 전체 backend 138건(분석 thread 예외를 실패 처리), 실제 MariaDB 통합 1건, 프런트 논리 19건·타입 검사·production build 579.87 kB·Ruff를 통과했다. 모델 누락/실패 시에도 얼굴 특징 생성과 탐지가 계속되는 회귀 검사를 통과했다. Chrome은 임시 활성 상태 1건 및 복원된 기본 비활성/Live Search/영상 클립 3건을 통과했다. native 결과는 data/reports/phase11-native.json, 실제 서비스 결과는 phase11-browser-ready.json·phase11-browser-disabled.json·phase11-browser-restore.json에 있다.

2026-10-04 KST 최종 상태는 API phase=11, DB head=0007_event_clips, 검출 cuda:0/얼굴 cuda, Re-ID disabled다. 현재 설정은 15 FPS·얼굴 간격 0.2초·ROI 4·비교 기준 0.7이고 실행 중인 카메라는 없다. 최종 스냅샷은 카메라 3개·인물 2명·등록 얼굴 3개이며 data/reports/phases8-11-final.json에 상태를 기록했다. Phase 9의 4채널 목표 미달과 Phase 10의 독립 정확도 자료 부족은 후속 검증 과제로 남는다. 이 Phase에서 카메라 간 이동경로와 실제 Re-ID 정확도 보정을 구현 완료했다고 보고하지 않는다.
