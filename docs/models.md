# Models and licensing

이 프로젝트는 비상업 시험용이다. 현재 사용한 시험 모델과 실행 라이브러리를 기록한다.

## Phase 1 시험 모델

Phase 1 당시 detector/얼굴 pretrained weights를 사용하지 않았다. `scripts/check_gpu.py`가 프로젝트 코드에서 생성한 64×64 MatMul ONNX graph를 사용했다. 입력은 `numpy.ones`이며 촬영 얼굴/영상 데이터가 아니다.

- Source: `scripts/check_gpu.py::build_smoke_model`
- ONNX IR: 10, opset: 17, float32, external weights 없음
- SHA256: `a8acce55391766422546b0a6d59e541594956a77e2c3e92a21367fa21c95bd1a`
- 사용 조건: 외부 pretrained model 가중치를 포함하지 않는 프로젝트 생성 시험 그래프

## 실제 설치한 실행 코드

다음 사용 조건은 설치한 배포 artifact metadata에 기재된 항목이다. 실제 사용 범위에는 각 공식 LICENSE가 적용된다.

| 패키지 | 고정 버전 | 배포 metadata의 license |
| --- | --- | --- |
| torch | 2.11.0+cu128 | BSD-3-Clause |
| torchvision | 0.26.0+cu128 | BSD |
| ultralytics (YOLO/ByteTrack 코드) | 8.4.171 | AGPL-3.0 |
| opencv-python | 5.0.0.93 | Apache 2.0 |
| lap | 0.5.12 | BSD-2-Clause |
| nvidia-ml-py | 13.615.71 | BSD |
| onnx | 1.23.1 | Apache-2.0 |
| onnxruntime-gpu | 1.26.0 | MIT License |
| qdrant-client | 1.19.1 | Apache-2.0 |
| fastapi | 0.142.2 | MIT |
| sqlalchemy | 2.1.1 | MIT |
| alembic | 1.20.0 | MIT |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| pwdlib | 0.3.1 | 배포 metadata 및 공식 LICENSE 참고 |

Angular/Bootstrap는 frontend package metadata의 MIT License를 따른다. NVIDIA CUDA/cuDNN 등의 binary runtime에는 각 NVIDIA wheel의 사용 조건이 적용된다. runtime과 모델 가중치의 사용 조건을 구분한다.

Qdrant native server는 공식 release를 설치했다.

- Version: 1.19.1
- Source: https://github.com/qdrant/qdrant/releases/download/v1.19.1/qdrant-x86_64-unknown-linux-gnu.tar.gz
- 공식 archive SHA256: `eef986e769d4d3e806dd2d546e1b4ecdd416211e54d34b4ed764fac7c58e1085`
- 설치 binary SHA256: `f1823c24376c4a5f2f665d42dc2f52fe4e537e7c2b070b8ae7da575d83327e3a`
- 배포 코드 사용 조건: [공식 LICENSE](https://github.com/qdrant/qdrant/blob/master/LICENSE)

## Phase 2 실제 탐지 모델과 시험 영상

COCO pretrained **YOLO11n** detection model의 person class(0)만 사용한다. 실행 코드는 Ultralytics 8.4.171, 추적은 같은 배포의 ByteTrack이다. 새 추적 생성/연결 threshold는 0.6/0.5, detector/low threshold는 0.1이며 낮은 점수의 box도 tracker로 전달한다. 별도 tracker ReID/얼굴 가중치는 없다.

- 가중치 출처: [Ultralytics assets v8.3.0/yolo11n.pt](https://github.com/ultralytics/assets/releases/download/v8.3.0/yolo11n.pt)
- SHA256: `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`
- 파일 크기: 5,613,764 bytes
- 사용 조건: Ultralytics 공식 문서가 명시하는 **AGPL-3.0** 경로로 비상업 시험에 사용한다. 비상업 목적 자체가 AGPL 조건을 면제하지 않는다. Enterprise license를 구매하거나 적용했다고 표시하지 않는다. [공식 YOLO11 모델 설명/라이선스](https://docs.ultralytics.com/models/yolo11/), [공식 AGPL LICENSE](https://github.com/ultralytics/ultralytics/blob/main/LICENSE).
- 실제 실행: `scripts/check_yolo.py`가 공식 시험 영상에서 person box 4개를 검출하고 **추론 backend의 실제 parameter device `cuda:0`**를 확인했다. Torch checkpoint의 초기 CPU 위치나 provider 목록만으로 CUDA 성공을 판정하지 않는다.

시험 영상은 [OpenCV 4.12.0 vtest.avi](https://github.com/opencv/opencv/blob/4.12.0/samples/data/vtest.avi)를 mp4v로 다시 인코딩한 768×576, 10 FPS MP4다. OpenCV 소스의 [공식 라이선스](https://github.com/opencv/opencv/blob/4.12.0/LICENSE)는 Apache-2.0이며, 외부 시험 영상은 출처를 기록하여 로컬 시험에만 사용한다. 원본/변환 파일은 저장소에 배포하지 않는다. 검색 정확도 평가용으로 인물/출현 구간의 정답을 부여한 데이터는 아니다.

| Artifact | SHA256 |
| --- | --- |
| 원본 vtest.avi | `45cddc9490be69345cbdab64ca583be65987e864ca408038e648db99e10516cf` |
| 시험 people.mp4 | `48a0a7beec9dc408b23b3e97ddc9d650948e356f25ae2ba2896862a581e7feca` |

`scripts/prepare_phase2.py`는 원본과 모델을 고정 SHA256에 대조한다. 다운로드 및 인코딩 metadata는 `data/reports/phase2-assets.json`, 실제 YOLO 검증 결과는 `data/reports/yolo.json`에 저장한다.

## Phase 3 실제 얼굴 모델

InsightFace 공식 **buffalo_l v0.7** 패키지에서 SCRFD face detector, 3D landmark 모델, ArcFace recognition 모델을 사용한다. InsightFace 전체 Python 배포는 설치하지 않고, 기존 OpenCV/NumPy/ONNX Runtime으로 고정된 모델의 tensor protocol을 처리한다. 코드 convention/ArcFace template의 MIT 고지는 `third_party/insightface/LICENSE`에 보관했다. 신규 Python 의존성은 없다.

- 가중치: [공식 buffalo_l v0.7 release](https://github.com/deepinsight/insightface/releases/download/v0.7/buffalo_l.zip)
- 코드 convention: [SCRFD](https://github.com/deepinsight/insightface/blob/v0.7/python-package/insightface/model_zoo/scrfd.py), [ArcFace](https://github.com/deepinsight/insightface/blob/v0.7/python-package/insightface/model_zoo/arcface_onnx.py), [landmark/pose](https://github.com/deepinsight/insightface/blob/v0.7/python-package/insightface/model_zoo/landmark.py), [alignment](https://github.com/deepinsight/insightface/blob/v0.7/python-package/insightface/utils/face_align.py)
- 코드 사용 조건: [MIT License, Copyright 2022 Jiankang Deng and Jia Guo](https://github.com/deepinsight/insightface/blob/v0.7/LICENSE)
- **가중치는 MIT가 아니며 공식 pretrained model의 비상업 연구용 사용 조건을 따른다.** 이 프로젝트의 비상업 시험 목적에 사용하고 상용 라이선스나 서명 검증 완료를 표시하지 않는다. [공식 모델 사용 조건](https://github.com/deepinsight/insightface#license)

| Artifact | SHA256 |
| --- | --- |
| buffalo_l.zip | `80ffe37d8a5940d59a7384c201a2a38d4741f2f3c51eef46ebb28218a7b0ca2f` |
| det_10g.onnx | `5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91` |
| 1k3d68.onnx | `df5c06b8a0c12e422b2ed8947b8869faa4105387f199c477af038aa01f9a45cc` |
| w600k_r50.onnx | `4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43` |
| meanshape_68.npy | `e32b77ecb2a39112eea88e727bd39db5319f5593590cecebcd3955d0b2d2257c` |

`scripts/prepare_phase3.py`는 archive/model/공식 mean shape와 sample의 SHA256을 고정하고 확인한다. fixed upstream pickle은 hash 확인 후 준비 스크립트에서만 NumPy로 변환하며, runtime은 `allow_pickle=False`로 고정된 `.npy`를 읽는다. 사용자 업로드를 pickle로 해석하지 않는다. archive와 weight, sample 및 변환 파일은 Git에 포함하지 않는다.

SCRFD는 person ROI를 종횡비 유지하여 320×320에 padding하고 RGB `(pixel-127.5)/128`로 입력한다. release의 detector input은 dynamic이지만 output metadata에는 640 기준 수가 남아 있어 shape annotation만 메모리에서 dynamic으로 바꾼다. 모델 tensor/weight는 바꾸지 않는다. 1k3d68은 192×192 RGB와 graph 내부 `bn_data` normalization을 사용한다. ArcFace는 5-point similarity alignment의 112×112 RGB `(pixel-127.5)/127.5` 입력을 사용하고 float32 512차원 출력을 L2 normalize한다. 버전은 `buffalo_l-v0.7-w600k_r50-4c06341c33c2`이며 다른 모델과 검색 공간을 공유하지 않는다.

각 모델의 실제 CUDA convolution profile과 공개 얼굴 입력에 대한 생성 결과는 `data/reports/face-gpu.json`에 저장한다. provider 목록만으로 CUDA 성공을 판정하지 않는다. 품질 기준은 현재 시험용 heuristic이며 자세 모델/landmark geometry가 정확한 가림 classifier를 대신한다고 표시하지 않는다.

얼굴 시험용 [Ultralytics 공개 zidane.jpg](https://github.com/ultralytics/assets/blob/main/im/zidane.jpg)의 SHA256은 `16d73869e3267a7d4ed00de8e860833bd1657c1b252e94c0c348277adc7b6edb`다. 해당 이미지의 제3자 사진 사용권이 라이브러리 코드의 AGPL/MIT에서 자동 허용된다고 해석하지 않는다. 로컬 smoke 시험에만 사용하고 사진·파생 자료를 저장소에 재배포하지 않는다. 자료 성격과 수동 정답의 범위는 [calibration-data.md](calibration-data.md)에 기록한다.

공식 참고: [Ultralytics 라이선스](https://www.ultralytics.com/license), [InsightFace 모델 사용 조건](https://github.com/deepinsight/insightface/blob/master/python-package/docs/model_zoo.md), [ONNX Runtime CUDA 요구 조건](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).

## Phase 5 RTSP 시험 도구

MediaMTX **v1.21.1 Linux amd64** 공식 바이너리를 localhost RTSP 장애/복구 시험에만 사용한다. AI 모델 가중치는 추가하지 않았다. Docker나 영구 서비스를 설치하지 않으며 `scripts/rtsp_fixture.py`가 공개 `face-smoke.mp4`를 FFmpeg로 송출하고 자신이 만든 프로세스를 종료한다. 시험 도구와 영상은 Git에서 제외한다.

- 출처: [공식 v1.21.1 release](https://github.com/bluenviron/mediamtx/releases/tag/v1.21.1), [Linux amd64 archive](https://github.com/bluenviron/mediamtx/releases/download/v1.21.1/mediamtx_v1.21.1_linux_amd64.tar.gz)
- archive SHA256: `653abc672a3e693f8d3b2717752492fdcfb8072291ec108d03d3dd857411b0ee`
- 실행 바이너리 SHA256: `2b45b2999f22c8a1ecd376ce407b6337b68466fdeac54a8fdc448a79f818474f`
- 사용 조건: 배포 archive에 포함된 MIT License, Copyright (c) 2019 aler9. 고지 원문을 `data/tools/mediamtx/LICENSE`에 함께 보관한다. 복제/배포 시 MIT의 저작권·허가 고지 조건을 유지한다. [공식 LICENSE](https://github.com/bluenviron/mediamtx/blob/v1.21.1/LICENSE)
- `prepare_phase5.py`는 archive checksum을 검증하고 파일별 hash/source/version을 `data/tools/mediamtx/manifest.json`에 기록한다. fixture는 실행 전 binary SHA256을 다시 확인한다.
