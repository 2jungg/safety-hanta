# Safety-Hanta: 산업 안전 모니터링 시스템

이 프로젝트는 산업 현장의 비디오 스트림을 실시간으로 분석하여 위험 요소를 감지하는 AI 시스템입니다. Qwen3-VL 멀티모달 모델을 활용하며, Kubernetes 기반의 확장 가능한 아키텍처로 구성되어 있습니다.

## 🏗️ 시스템 아키텍처 (Kubernetes)

본 시스템은 Kubernetes 상에서 마이크로서비스 형태로 동작하며, 각 컴포넌트는 다음과 같은 역할을 수행합니다.

### 1. Node 구성
*   **Control Plane**: 클러스터 관리 및 오케스트레이션 담당.
*   **Worker Node (GPU)**: 고성능 연산이 필요한 AI 추론(`inference`) 파드가 배치되는 노드. NVIDIA GPU가 할당됩니다.
*   **Worker Node (General)**: 데이터 수집(`capture`) 및 메시지 큐(`redis`) 등 I/O 중심의 파드가 배치됩니다.

### 2. Pod 역할
*   **🎥 rtsp-sim (Simulator)**
    *   CCTV 역할을 하는 가상 비디오 스트림을 생성하여 송출합니다.
    *   다양한 산업 현장 시나리오 영상을 RTSP 프로토콜로 제공합니다.
*   **📥 capture-worker**
    *   RTSP 스트림을 받아 비디오 파일로 저장(Capture)합니다.
    *   저장된 파일의 경로와 메타데이터를 Redis 큐에 적재합니다.
*   **🧠 inference (VLLM)**
    *   핵심 AI 엔진입니다. Redis에서 작업 요청을 가져와 Qwen3-VL 모델로 분석합니다.
    *   "안전", "위험", "경고" 등의 상태를 판단하고 구체적인 위험 요소를 리포팅합니다.
*   **📨 redis**
    *   `capture-worker`와 `inference` 사이의 버퍼 역할을 하는 메시지 브로커입니다.
    *   작업 대기열(Queue)을 관리하여 시스템 부하를 조절합니다.

---

## ⚡ 핵심 최적화 기술 (Methodologies)

고성능 실시간 처리를 위해 다음과 같은 기술적 방법론들이 적용되었습니다.

### 1. Prefix Caching (VLLM)
*   **문제**: Few-shot 프롬프팅(예시를 여러 개 보여주는 방식)은 프롬프트 길이가 길어져 연산 비용이 높습니다.
*   **해결**: **Prefix Caching**을 도입하여, 시스템 프롬프트와 고정된 Few-shot 비디오 예시들에 대한 KV Cache를 메모리에 상주십니다.
*   **효과**: 매 요청마다 반복되는 앞부분(Prefix)의 연산을 건너뛰고, 새로운 입력(Query)만 처리하므로 추론 속도가 비약적으로 향상됩니다.

### 2. Threading & Pipeline (Async I/O)
*   **문제**: 비디오 디코딩과 전처리(Pre-processing)는 CPU를, AI 추론은 GPU를 사용합니다. 순차적으로 처리하면 GPU가 CPU 작업을 기다리는 유휴 시간(Idle)이 발생합니다.
*   **해결**: `inference` 서비스 내에서 **Threading**을 사용하여 생산자(Preparer)-소비자(Main Loop) 패턴을 구현했습니다.
    *   `Preparer Thread`: CPU로 비디오를 읽고 텐서로 변환하여 큐에 쌓습니다.
    *   `Main Loop`: GPU로 준비된 배치를 가져와 즉시 추론합니다.
*   **효과**: CPU와 GPU가 병렬로 동작하여 GPU 가동률(Utilization)을 극대화합니다.

### 3. Zero-Copy (Reference Passing via Shared Volume)
*   **문제**: 고해상도 비디오 데이터를 Redis를 통해 바이트(Byte) 단위로 전송하면 네트워크 오버헤드와 직렬화/역직렬화 비용이 매우 큽니다.
*   **해결**: **Shared Volume** 전략을 사용하여 데이터를 복사하지 않습니다.
    *   `capture-worker`는 공유 볼륨에 파일을 쓰고, Redis에는 오직 **파일 경로(String Path)**만 보냅니다.
    *   `inference`는 해당 경로에서 파일을 직접 읽습니다(Read-only).
*   **효과**: 불필요한 메모리 복사를 방지(Zero-copy)하고 시스템 전체 대역폭을 절약합니다.