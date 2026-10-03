# Phase 9 다중 카메라 성능 측정 및 처리 개선

`scripts/benchmark.py`의 기존 기본값은 실시간 1채널을 유지하며 `--channels 1 2 4`와 `--mode offline_throughput`을 추가했다. 실제 API 시험은 임시 카메라만 만들고 정리하며 다른 카메라가 실행 중이면 중단한다. 사용자 FPS/비교 기준은 바꾸지 않는다. 채널마다 입력/캡처/검출/얼굴 ROI/embedding FPS, 최대 인원, 한 칸 queue와 버린 프레임, 평균/P95 서버 지연과 inference 시간을 기록한다. 모델 SHA·버전·resize·precision·CUDA, GPU 사용률/메모리와 프로세스 RSS, 녹화/preview 조건도 함께 기록한다. 서버 지연은 디코딩된 프레임부터 결과/JPEG까지이며 카메라 encoder·네트워크·브라우저는 제외한다. 평균/P95는 카메라의 최근 최대 600표본이다. NVML은 GPU 전체 값이며 다른 프로세스의 사용도 포함한다.

GPU는 공유 scheduler 하나가 사용한다. 준비된 카메라의 최신 프레임을 최대 4개씩 검출하고 카메라 순서를 순환한다. 묶음을 채우기 위한 대기는 없다. 얼굴 처리가 무거우면 100ms 예산에 맞춰 묶음을 줄이고 이전 카메라의 처리가 끝난 직후 최신 프레임을 선택해 불필요한 지연을 줄인다. 트래커·얼굴 cache는 카메라/session마다 분리하고 inference 중 세션이 회전하면 이전 결과를 폐기한다. 같은 프레임의 얼굴 샘플 검색에는 SQL 자격 snapshot 하나를 재사용하되 다음 프레임에서 다시 검사한다. TTL 캐시를 도입하지 않았고 이벤트 저장 시에도 DB 기준을 재확인한다. OpenCV 연산과 FFmpeg decoder는 각각 기본 2개 thread로 제한해 여러 입력의 CPU 스레드 경쟁을 줄인다. [OpenCV 공식 video I/O 속성](https://docs.opencv.org/4.x/d4/d15/group__videoio__flags__base.html)의 FFmpeg 전용 CAP_PROP_N_THREADS를 사용한다.

RTX 3070 Ti에서 검출만 따로 5회 warmup+30회 반복했다. FP32 묶음 1/2/4의 평균은 7.52/9.46/12.33ms(프레임당 7.52/4.73/3.08ms)였다. FP16은 7.96/9.57/12.59ms로 이득이 없어 기본값을 FP32로 유지했다. `YOLO_FP16=true`는 시험 옵션이며 실제 CUDA 파라미터 float16 및 각 채널의 두 사람 검출을 확인했다. 얼굴 ONNX 모델 precision은 유지한다. [Ultralytics predict 문서](https://docs.ultralytics.com/modes/predict/)와 설치된 고정 버전 8.4.171의 `quantize` 설정을 사용했다.

실행:

```bash
.venv/bin/python scripts/benchmark.py --video data/videos/phase9-face-1080p.mp4 --channels 1 2 4 --duration 15 --require-embeddings --output data/reports/phase9-1080p-final.json
.venv/bin/python scripts/benchmark.py --video data/videos/phase9-face-1080p.mp4 --channels 1 2 4 --duration 10 --mode offline_throughput --require-embeddings --output data/reports/phase9-offline-final.json
.venv/bin/python scripts/benchmark.py --video data/videos/phase9-face-1080p.mp4 --channels 1 2 4 --duration 10 --mode offline_throughput --disable-faces --output data/reports/phase9-offline-detector.json
```

실시간 시험 입력은 공개 사진 기반 MP4를 1920×1080/30 FPS로 확대·반복한 24초 영상이고 채널별 검출 인원은 2명이다. 일반 CCTV나 움직이는 인물의 정확도/성능을 대표하지 않는다. 모든 채널에서 실제 CUDA 얼굴 embedding을 요구했다. 실제 저장된 설정 FPS 15, 얼굴 간격 0.2초, ROI 4, 비교 기준 0.7을 보존했다. 녹화와 DB 검색을 켠 실시간 결과, gallery/event/clip을 제외한 파일 최대 처리량은 서로 다른 workload이므로 직접 같은 성능으로 비교하지 않는다. 얼굴 검사·추적 간격은 offline에서도 유지하며 detector-only 결과를 얼굴 검색 성능으로 해석하지 않는다.

신규 의존성과 DB migration은 없고 head 0007_event_clips를 유지한다. 주요 변경은 detector/runtime/gallery/faces, benchmark/benchmark_multi 및 관련 설정·테스트다. 검증 결과와 최종 수치는 아래에 추가한다.

## 실제 실시간 측정 결과

|채널|이전 FPS/채널|최종 FPS/채널|이전 P95 ms|최종 P95 ms|
|---|---|---|---|---|
|1|5.0|6.2|273.5|236.57|
|2|2.4/2.33|2.8/2.73|244.56/247.72|233.72/234.22|
|4|1.13/1.13/1.13/1.13|1.2/1.2/1.27/1.2|254.3/265.14/262.09/257.67|241.4/241.13/244.33/245.07|

1채널: PyTorch allocated/reserved 74.1/120.0 MiB, worker RSS 2372.5 MiB. NVML 전체 GPU peak 2800.9 MiB, 평균 사용률 9.9%. 모든 채널의 max_people=2, 실제 embedding 생성 및 인증된 preview를 확인했다.

2채널: PyTorch allocated/reserved 74.1/120.0 MiB, worker RSS 2420.6 MiB. NVML 전체 GPU peak 2800.9 MiB, 평균 사용률 13.4%. 모든 채널의 max_people=2, 실제 embedding 생성 및 인증된 preview를 확인했다.

4채널: PyTorch allocated/reserved 74.1/120.0 MiB, worker RSS 2533.9 MiB. NVML 전체 GPU peak 2800.9 MiB, 평균 사용률 12.6%. 모든 채널의 max_people=2, 실제 embedding 생성 및 인증된 preview를 확인했다.

4채널 5~10 FPS 목표에는 미달했다. 이 시험 조건의 실시간 얼굴 검사 부하는 검출만의 처리량과 다르다. 평균 FPS 개선은 제한적이며 지연·메모리 한도를 개선/기록했다. 처음 고정 4개 묶음+FP16 trial에서는 P95가 약 900ms로 증가해 폐기하고 얼굴 비용에 따라 묶음을 줄이는 최종 정책을 채택했다. 부하와 환경에 따라 성능이 달라지므로 목표 달성을 보장하지 않는다.

실제 자료는 data/reports/phase9-1080p-before.json, phase9-1080p-after.json(폐기한 FP16 trial), phase9-1080p-final.json, phase9-precision.json 및 offline 보고서에 기록한다. 사용자 설정 값과 기존 카메라는 보존했다.

## 파일 최대 처리량

|채널|얼굴 활성 FPS/채널|얼굴 비활성 FPS/채널|
|---|---|---|
|1|13.05|68.03|
|2|5.57|43.83|
|4|4.38|25.13|

전체 backend 120건(분석 thread 예외도 실패 처리), 실제 MariaDB 통합 1건 및 Ruff를 통과했다. 배치 실행 도중 한 카메라 session이 회전해도 다른 카메라 결과와 local track ID가 보존되는 회귀 검사를 추가했다.

서비스 반영 후 Chrome RTSP 중단/재연결/중지와 CUDA 이벤트 클립 저장·재생 2건을 통과했다. RTSP 4대의 실제 네트워크 성능과 움직이는 인물의 독립 정확도는 이번 정지 사진 MP4 성능 시험으로 검증하지 않았다. 기존 Node/Angular 코드는 이 Phase에서 변경하지 않았다.
