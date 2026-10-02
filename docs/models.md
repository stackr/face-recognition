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

## 후속 Phase의 모델 선정 예정

- Phase 3: InsightFace와 호환되는 detector/ArcFace ONNX 가중치를 선정한다. 라이브러리 코드의 MIT License와 공식 pretrained model의 비상업 연구용 제한을 구분하고, 실제 시험 목적이 허용 범위에 해당하는지 확인한다.
- 실제 모델을 선택하기 전에는 모델 license 검토 완료나 해당 모델의 CUDA inference 검증 완료로 표시하지 않는다.

공식 참고: [Ultralytics 라이선스](https://www.ultralytics.com/license), [InsightFace 모델 사용 조건](https://github.com/deepinsight/insightface/blob/master/python-package/docs/model_zoo.md), [PyTorch CUDA wheel](https://pytorch.org/get-started/previous-versions/), [ONNX Runtime CUDA 요구 조건](https://onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html).
