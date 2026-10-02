# Phase 3 verification report

검증일: 2026-10-02 (Asia/Seoul). 비상업 시험 프로젝트의 Phase 3을 구현했다. 기존 계정/DB schema/등록 RTSP와 카메라 설정을 유지하고, 기존에 중지된 카메라는 자동으로 시작하지 않았다. 시험은 공개 sample과 자신이 만든 임시 MP4 카메라로 수행했으며 시험 카메라와 업로드 파일은 종료 시 삭제했다.

## 구현한 동작

- 공유 GPU worker에 SCRFD 얼굴 detector, 1k3d68 landmark/pose, ArcFace w600k_r50 ONNX session을 추가했다. 모델 SHA256은 준비 및 startup에서 고정 값에 대조한다.
- person ROI에서 얼굴과 5-point landmark를 찾고 원본 크기·Laplacian 선명도·밝기·confidence·자세·landmark geometry·종합 quality를 검사한다. pose는 작은 얼굴에서 생략한다. 여러 후보 얼굴의 track 연결은 보류한다.
- 112×112 similarity alignment 후, 품질 hard gate를 통과하고 이전 best보다 좋은 얼굴에서만 float32 512D/L2 embedding을 생성한다. 별도 라이브러리 설치 없이 이미 고정된 ONNX Runtime/OpenCV/NumPy를 사용했다.
- 카메라별 0.5초 track sampling/4 ROI frame budget/100 track face cache 상한을 적용했다. 같은 얼굴의 특징을 매 frame 다시 만들지 않는다. stop/loop/end/error/restart/track expiry에서 이전 세션의 얼굴 상태를 이어 붙이지 않는다.
- 인증과 카메라 grant 및 session UUID를 검사하는 best-face JPEG endpoint를 추가했다. 상태/UI에 raw embedding을 전달하지 않는다. 원본 얼굴/특징을 DB나 Qdrant에 저장하지 않는다.
- Angular 영상 분석에 얼굴 검사량, 생성 횟수, 최적 얼굴 썸네일, quality/자세/제외 사유를 표시했다. 얼굴 annotation은 실제 분석한 frame에만 그린다.
- calibration 정답 schema와 수동 익명 subject/출현 구간/positive·negative pair 예제를 준비했다. 같은 촬영 group의 split 중복을 차단한다.

## 실제 CUDA와 반복 실행

Python 3.12.3 / PyTorch 2.11.0+cu128 / CUDA runtime 12.8 / cuDNN 9.19 / ONNX Runtime GPU 1.26.0 / RTX 3070 Ti 8GB / driver 575.51.03을 사용했다.

`scripts/check_face_gpu.py`는 모델별 startup profiling에서 실제 CUDA convolution을 확인하고 공개 이미지에서 얼굴 탐지/자세/정렬/ArcFace까지 실행했다. provider 문자열만으로 GPU 성공을 표시하지 않는다. CPU fallback은 별도 상태이며 CUDA 검증으로 성공 처리하지 않는다.

| 모델 | CUDA node | CUDA Conv | 입력 |
| --- | ---: | ---: | --- |
| det_10g | 141 | 58 | 1×3×320×320 |
| 1k3d68 | 143 | 54 | 1×3×192×192 |
| w600k_r50 | 130 | 53 | 1×3×112×112 |

공개 원본 이미지에서 정면에 가까운 얼굴의 quality는 0.9128, embedding norm은 1.0이었다. 다른 얼굴은 quality 0.7142였지만 yaw 약 68°로 hard gate에서 제외하여 embedding을 만들지 않았다. 이 두 사례는 gate 동작 확인이며 정확도 평가가 아니다.

검증 중 ONNX의 반복 추론에서 arena limit 고갈을 재현했다. 최종 설정은 session별 1 GiB arena, `kNextPowerOfTwo`, `enable_mem_pattern=False`, unified CUDA stream 및 cuDNN workspace 제한이다. 이 설정으로 별도 GPU worker 스레드에서 80회 반복, 얼굴 ROI 160회, 품질 통과/제외 각각 80회, 동일 best의 embedding 1회를 확인했다. `check_face_pipeline.py`는 기능/메모리 회귀 검증이고 real-time 처리량 측정이 아니다.

## 1채널 real-time benchmark

`scripts/benchmark.py --video data/videos/face-smoke.mp4 --duration 20 --require-embeddings --output data/reports/phase3-benchmark.json`으로 측정했다. 다른 카메라/검증 작업이 종료된 뒤 실행했으며 측정 중 다른 카메라가 시작되면 중단하도록 했다. 공개 사진을 반복한 smoke MP4이고, 독립 CCTV 검색 자료나 움직이는 얼굴의 성능을 대표하지 않는다.

| 조건/지표 | 결과 |
| --- | ---: |
| 입력 | 1280×720, 10 FPS, 40초 smoke MP4 |
| 측정 시간 / 채널 | 20초 / 1 |
| YOLO11n / SCRFD resize | 640 / 320 |
| 최대 사람 수 | 2 |
| captured / processed / dropped / pending | 200 / 99 / 100 / 1 |
| 사람 탐지 처리 FPS | 4.95 |
| 얼굴 검사 frame FPS / ROI 검사 초당 | 1.65 / 3.30 |
| 얼굴 ROI / 품질 통과 / 제외 | 66 / 33 / 33 |
| ArcFace embedding 생성 | 1 |
| 얼굴 검사 구간 평균 시간 | 13.39 ms |
| YOLO 평균 시간 | 15.85 ms |
| decoded capture → 결과/JPEG 평균 / P95 | 68.42 / 119.05 ms |
| 처음 결과를 polling으로 확인 | 1.036초 |
| Torch GPU allocated / reserved | 74.1 / 120.0 MiB |
| NVML 전체 GPU memory peak | 2666.8 MiB |
| NVML 전체 GPU 평균 / peak utilization | 13.05 / 28% |
| 측정 중 preview viewer | 0 |
| 종료 직전 인증 preview / 112px 얼굴 thumbnail | 모두 통과 |

목표 sampling이 5 FPS이고 입력이 10 FPS이므로 중간 frame을 의도적으로 교체한다. `face_analysis_fps`는 ROI를 검사한 frame 수이고 `face_roi_fps`는 ROI 요청 수다. 동일한 best를 유지하므로 특징 생성 수가 ROI 수보다 적다. Torch memory는 ONNX allocation을 포함하지 않는다. NVML은 다른 프로세스를 포함한 전체 GPU이고 utilization은 1초 간격 표본이다. latency는 서버가 decode한 frame의 capture부터 분석/JPEG 준비까지이며 camera encoder/브라우저 지연은 포함하지 않는다.

Phase 2 baseline은 다른 영상(768×576, 사람 최대 8명)에서 4.95 FPS/P95 132.54ms였다. 입력 영상·사람 수가 달라 Phase 3의 수치로 얼굴 분석 비용의 직접적인 전후 증감이나 최적화 효과를 주장하지 않는다.

## 검증 결과와 artifact

- 단위 테스트 26개 통과: alignment 변환, 512D 정규화, size/blur/brightness/pose gate, sampling/best 갱신/expiry/용량/검사 배분, person ROI 좌표, 다중 얼굴 보류, thumbnail 인증/grant/해제/비활성화, stop/restart/loop/end의 얼굴 상태 초기화 및 ground truth 검증.
- 실제 MariaDB/Qdrant 통합 테스트 1개 통과. migration head는 기존 `0003_camera_permissions`이며 Phase 3은 새 DB schema를 필요로 하지 않는다.
- Chrome E2E 3개 통과: 기존 로그인/CRUD/MP4 분석, 실제 GPU 얼굴 특징과 112px thumbnail, 비로그인 거부, 중지/새 session/이전 thumbnail URL의 404.
- Ruff lint/format 및 Angular typecheck/production build 통과. build initial bundle 478.81 kB.
- 기존 Starlette `httpx` TestClient deprecation warning 1개는 실행 실패가 아니며 관련 dependency 변경은 하지 않았다.

로컬 artifact는 Git에서 제외된다.

| 경로 | 내용 |
| --- | --- |
| `data/reports/phase3-assets.json` | 공식 모델/sample 출처, 크기, SHA256 |
| `data/reports/face-gpu.json` | 실제 CUDA 모델별 profile 및 공개 얼굴 특징 검증 |
| `data/reports/face-pipeline.json` | 80회 공유 모델/추론 스레드 회귀 시험 |
| `data/reports/phase3-benchmark.json` | 얼굴 분석 포함 1채널 측정 |
| `data/reports/phase3-runtime.json` | 최종 native 서비스/모델 장치와 sanitized 카메라 상태 |
| `data/calibration/phase3-smoke.json`, `schema.json` | 수동 정답 예제와 validation schema |
| `data/screenshots/phase3-live.png` | 공개 smoke MP4의 Angular 화면 |

## 현재 범위

얼굴 quality/pose/가림 heuristic은 아직 독립 데이터로 보정하지 않았다. 반복 사진과 그 변형은 전체적으로 한 source group이며 FAR/FRR/precision/recall은 `unavailable`이다. [정답 자료 형식 및 수집 기준](calibration-data.md), [모델 출처/사용 조건](models.md), [구성](architecture.md)을 참고한다.

등록 인물/복수 reference 등록, cosine similarity 검색, DB/Qdrant vector 저장, MatchEvent와 얼굴 등록 자료의 감사·삭제·보관 정책은 Phase 4에서 구현한다. 4채널 성능, 다양한 사람/각도의 실제 CCTV 얼굴 정확도, 자동 RTSP reconnect는 아직 검증하지 않았다. 현재 `얼굴 특징 준비됨`은 동일인 확인 결과를 의미하지 않는다.

## MP4 카메라 저장 수정 (2026-10-02)

RTSP 주소를 입력한 뒤 입력 유형을 MP4로 전환하면 숨겨진 주소가 요청에 남아, 유효하지 않은 RTSP 값 때문에 저장 API가 422를 반환하는 문제를 Chrome에서 재현했다. MP4 저장 요청은 RTSP 값을 비우고 API도 MP4에 해당하지 않는 RTSP 값을 검증·저장하지 않도록 수정했다. 카메라 이름 누락과 CCTV 주소 누락에는 화면에서 구체적인 안내를 제공한다. RTSP 유형의 주소 검증은 유지한다.

수정 후 단위 테스트 29개와 Chrome E2E 3개가 통과했다. MP4 신규 저장(201)부터 업로드·GPU 영상 분석·중지까지 확인했으며, lint/format/typecheck/production build도 통과했다. 변경한 API를 native 서비스에 반영했고 frontend build initial bundle은 479.22 kB다.
