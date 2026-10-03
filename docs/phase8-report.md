# Phase 8 이벤트 영상 클립

검색 이벤트를 DB에 먼저 기록하고, 별도 녹화 작업에서 검출 전 5초/후 10초를 MP4로 만든다. GPU 분석·검색·WebSocket은 후속 구간 대기나 FFmpeg 완료를 기다리지 않는다. 초기 상태는 pending이고 결과는 ready/failed이며 이전 이벤트는 disabled로 유지한다.

카메라·스트림 세션마다 1초 단위 MJPEG 디스크 세그먼트와 실제 캡처 시각을 보관한다. 분석용 최신 프레임과 녹화용 한 칸 대기열을 분리하고 압축은 별도 thread에서 수행한다. JPEG 프레임마다 독립적으로 디코딩할 수 있어 GOP 경계 손실이 없으며, 중첩 이벤트는 같은 세그먼트를 공유한다. FFmpeg H.264 원본 segment 복사 방식도 검토했으나 현재 OpenCV 캡처의 세션·시각과 일치시키는 추가 입력 연결을 피하기 위해 MJPEG를 선택했다. 최종 MP4는 H.264, 최대 1280px/10 FPS, 음성 없이 저장한다. [FFmpeg segment 문서](https://ffmpeg.org/ffmpeg-formats.html#segment_002c-stream_005fsegment_002c-ssegment)의 keyframe 제한을 고려한 선택이다.

기본 상한은 카메라 전체 버퍼 32 MiB, 버퍼·조립 임시 파일·클립 합계 1 GiB, 디스크 여유 100 MiB, 보관 7일이다. 인코딩 공간을 예약하고 출력 크기를 제한한다. 파일/폴더는 0600/0700이며 worker 재기동 때 자체 UUID 임시 폴더를 정리하고 남은 pending을 실패 상태로 복구한다. 보관 만료 및 이벤트/인물/카메라 삭제 시 파일을 정리한다. 구간 부족·프레임 누락은 partial, 실제 전후 초수, 최대 간격과 오류로 표시한다. 짧은 MP4 종료나 RTSP 재연결 시 다른 세션의 영상을 이어 붙이지 않는다.

`GET/HEAD /api/events/{id}/clip`은 로그인·카메라 및 인물 권한·삭제/만료 여부를 확인하며 Range와 no-store를 지원한다. Live Search 이벤트 카드에서 상태와 구간을 보고 브라우저 영상 플레이어로 재생한다. 완료/실패는 기존 change journal을 통해 WebSocket·HTTP 재연결에 반영한다.

실행:

```bash
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/python scripts/restart_analysis.py
.venv/bin/pytest -q -m 'not integration'
CCTV_RUN_INTEGRATION=1 .venv/bin/pytest -q -m integration
cd frontend && npx playwright test phase8.spec.ts
```

백엔드 119건, MariaDB 통합 1건, 프런트 논리 19건·타입 검사·production build(579.47 kB), Ruff를 통과했다. 실제 CUDA 후보 생성→일부 구간 클립 완료→Chrome 재생→Range 206→미로그인 401→삭제 후 404를 확인했다. FFmpeg/ffprobe를 이용한 별도 시험에서 정확한 5초+10초 구간을 검증했고 저장 한도·권한·보관 만료·삭제 중 완료 경쟁도 확인했다. DB head는 0007_event_clips이고 재기동 당시 실행 중인 사용자 카메라는 없었다. 상세 시험 자료는 private data/reports/phase8-browser.json과 data/screenshots/phase8-clip.png에 있다.

주요 변경 파일은 worker/clips.py, worker/runtime.py·api.py, services/events.py, models/events.py, api/events.py, 0007_event_clips.py, event-panel.component.ts 및 관련 테스트다. 새 모델·Python/npm 의존성을 추가하지 않았으며 시스템 FFmpeg 8.0.1/libx264를 사용한다. 현재 FFmpeg는 GPL/nonfree 옵션이 포함된 로컬 시험 설치이므로 배포 라이선스 승인을 뜻하지 않는다. 독립 인식 정확도 측정은 Phase 10 범위다.

전체 Chrome 11건은 로그인 제한(동일 IP 10회/5분)을 고려해 나누어 실행했다. 첫 실행에서 9건이 통과했고, 제한으로 실패한 클립·기능 설정 2건을 서비스 준비 완료 후 별도로 재검증했다. 재기동 중 시작한 중간 검사는 worker 준비 전 503으로 중단되었으며 최종 결과와 구분한다. 일반 회귀 실행도 10건 이하씩 나누고 로그인 제한 창이 지난 뒤 다음 묶음을 실행한다.
