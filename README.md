# Safety-Hanta (산업 안전 모니터링 시스템)

본 프로젝트는 RTSP 비디오 스트림을 실시간으로 분석하여 산업 현장의 안전을 감지하는 시스템입니다. RTSP 시뮬레이터, 비디오 캡처, 그리고 AI 추론(Inference)의 3단계 파이프라인으로 구성되어 있습니다.

## 🚀 프로젝트 개요

이 시스템은 다음과 같은 흐름으로 동작합니다:
1.  **RTSP Simulator**: 저장된 비디오 파일을 RTSP 스트림으로 송출합니다.
2.  **Capture Worker**: RTSP 스트림을 수신하여 5초 단위의 비디오 클립으로 저장하고 Redis 큐에 적재합니다.
3.  **Inference Worker**: Redis 큐에서 비디오 데이터를 받아 VLLM 기반의 멀티모달 모델(Cosmos-Reason1)을 통해 위험 상황을 분석합니다.

## 🏗️ 시스템 아키텍처

데이터 흐름은 다음과 같습니다:

`RTSP Simulator` -> (RTSP Stream) -> `Capture Worker` -> (Redis Queue) -> `Inference Worker`

### 1. RTSP Simulator (`k8s/02-rtsp-sim.yaml`)
-   `mediamtx`와 `ffmpeg`를 사용하여 RTSP 서버를 구축합니다.
-   `/videos` 디렉토리에 있는 MP4 파일들을 `rtsp://<host>:8554/camX` 주소로 무한 반복 송출합니다.
-   최대 10개의 카메라 스트림을 시뮬레이션합니다.

### 2. Capture Worker (`src/capture/main.py`)
-   Python 기반의 서비스로, 여러 RTSP 주소에 동시 접속합니다.
-   스트림을 5초 간격으로 버퍼링하여 로컬에 임시 저장 후, Base64로 인코딩합니다.
-   인코딩된 영상 데이터와 메타데이터(stream_id, timestamp)를 Redis List(`video_stream_queue`)에 Push합니다.

### 3. Inference Worker (`src/inference/main.py`)
-   Redis 큐를 모니터링하다가 새로운 비디오 데이터가 들어오면 즉시 가져옵니다 (BPOPP).
-   `Cosmos-Reason1-7B` 모델을 사용하여 비디오 내용을 분석합니다.
-   산업 안전 관련 프롬프트를 사용하여 영상 내 위험 요소를 텍스트로 리포팅합니다.
-   GPU 가속(NVIDIA)을 적극적으로 활용합니다.

## 🛠️ 설치 및 실행 (Installation & Running)

이 프로젝트는 Kubernetes (Kind) 환경에서 실행되도록 구성되어 있습니다.

### 사전 요구 사항
-   Docker
-   Kind (Kubernetes in Docker)
-   NVIDIA GPU 및 해당 드라이버 (GPU 가속을 위해 필수)

### 실행 방법

간편하게 제공되는 스크립트를 통해 원클릭으로 배포할 수 있습니다.

```bash
sh run.sh
```

이 스크립트는 다음 작업을 수행합니다:
1.  기존 Kind 클러스터를 정리하고 새로 생성합니다.
2.  필요한 Docker 이미지(`video-capture`, `cosmos-reason1-server`)를 노드에 로드합니다.
3.  Kubernetes 매니페스트(`k8s/`)를 일괄 적용하여 포드들을 실행합니다.

## ⚙️ 주요 환경 변수

| 컴포넌트 | 변수명 | 설명 | 기본값 |
| --- | --- | --- | --- |
| **Common** | `REDIS_HOST` | Redis 서비스 호스트 | `redis-service` |
| **Common** | `REDIS_PORT` | Redis 서비스 포트 | `6379` |
| **Capture** | `RTSP_URLS` | 수집할 RTSP 스트림 URL 목록 (콤마로 구분) | - |
| **Inference** | `MODEL_PATH` | AI 모델 경로 | `/app/saved_models...` |

## 📂 디렉토리 구조

```
safety-hanta/
├── k8s/                # Kubernetes 배포 매니페스트
├── src/
│   ├── capture/        # 비디오 수집 로직 (RTSP -> Redis)
│   └── inference/      # AI 추론 로직 (Redis -> VLLM)
├── videos/             # 시뮬레이션용 비디오 샘플
├── run.sh              # 통합 배포 스크립트
└── ...
```
