# Safety-Hanta: Industrial Safety Monitoring System with Visual Reasoning

이 프로젝트는 산업 현장의 비디오 스트림을 실시간으로 분석하여 위험 요소를 감지하고, 시각적 추론(Visual Reasoning)을 통해 구체적인 위험 상황을 리포팅하는 AI 시스템입니다.  
**Qwen3-VL** 멀티모달 모델을 활용하며, Kubernetes (Kind) 기반의 확장 가능한 MSA(Microservices Architecture)로 구성되어 있습니다.

---

## 🏗️ 1. 시스템 아키텍처 (System Architecture)

본 시스템은 **Kind (Kubernetes in Docker)** 클러스터 위에서 동작하며, 역할별로 노드(Node)와 파드(Pod)가 분리되어 있습니다.

### 1.1 Node 구성 (Nodes)
| Node Name | Role | Description |
| :--- | :--- | :--- |
| **control-plane** | Master | 클러스터 관리 및 오케스트레이션 |
| **worker (Generic)** | Application | 가벼운 로직(`logic`), 대시보드(`dashboard`), 알림(`notification`) 등 CPU 위주의 서비스 실행 |
| **worker2 (Inference)** | GPU/AI | **NVIDIA GPU**가 할당된 노드로, 고성능 AI 추론(`inference`) 파드가 실행됨 |
| **worker3 (Capture)** | I/O Heavy | 다수의 CCTV 스트림을 수신하고 저장하는 `capture-worker`들이 실행됨 (I/O 부하 분산) |

### 1.2 Pod 역할 (Components)
| Component | Pod Name | Description |
| :--- | :--- | :--- |
| **RTSP Simulator** | `rtsp-sim` | (Optional) 테스트용 가상 CCTV 스트림을 생성하여 송출 |
| **Capture Worker** | `capture-worker` | RTSP 스트림을 수신하여 비디오 파일로 저장하고, 메타데이터를 Redis에 적재 |
| **Message Broker** | `redis` | 시스템 컴포넌트 간의 비동기 통신을 담당하는 메시지 큐 (작업 대기열 관리) |
| **AI Inference** | `inference` | Redis 큐에서 작업을 가져와 **VLM(Cosmos-Reason1, Cosmos-Reason2, Qwen3)** 모델로 위험 상황을 분석 및 추론 |
| **Logic/Notification** | `logic` / `noti` | 추론 결과를 후처리하고, 위험 감지 시 Telegram 등으로 알림 발송 |
| **Dashboard** | `dashboard` | 실시간 모니터링 현황 및 위험 발생 로그를 시각화하여 제공하는 웹 UI |

---

## 🌐 2. RTSP 연결 및 네트워크 구조 (Connection & Network)
이 프로젝트는 원격지의 RTSP 소스를 안전하게 가져오기 위해 **SSH Tunneling**을 사용하며, Kubernetes 클러스터 내부에서 호스트 네트워크를 통해 이 터널에 접근합니다.

### 2.1 연결 흐름 (Connection Flow)
1.  **SSH Tunneling (`connect.sh`)**:
    *   로컬 머신(Host)의 `8558` 포트를 원격지 서버(`202.31.52.237`)의 `558` 포트로 포워딩합니다.
    *   Command: `ssh -L 8558:192.168.231.200:558 ...`
2.  **Cluster Access (`k8s`)**:
    *   Kind 클러스터 내부의 `capture-worker` 파드는 `172.18.0.1`(Host Gateway)을 통해 호스트의 `8558` 포트에 접근합니다.
    *   **Result**: 파드 내부에서 `rtsp://172.18.0.1:8558/...` 주소로 원격지 카메라 스트림을 획득합니다.

> [!IMPORTANT]
> **`172.18.0.1`**은 Kind 네트워크에서 호스트 머신을 가리키는 Gateway IP입니다. 이 IP를 통해 호스트의 SSH 터널을 경유하여 원격지 RTSP 소스에 접근합니다.

---

## 🚀 3. 실행 가이드 (Execution Guide)

시스템을 구동하기 위해서는 **SSH 터널링**과 **Kubernetes 배포** 두 단계가 필요합니다. 터널링 세션 유지를 위해 `screen` 사용을 권장합니다.

### Step 1: RTSP 연결 (SSH Tunneling)
`screen`을 사용하여 백그라운드에서 SSH 터널을 유지합니다.

```bash
# 1. 새로운 screen 세션 생성
screen -S tunnel

# 2. 연결 스크립트 실행 (비밀번호 입력 필요, 현재 DP 컴퓨터의 비밀번호는 해민님 기록 확인)
./connect.sh

# 3. 연결 확인 후 세션 분리 (Detach)
# 키보드에서 'Ctrl + A', 그리고 'D'를 순서대로 누르세요.
```

### Step 2: 시스템 배포 (Deploy System)
메인 터미널에서 실행 스크립트를 구동하여 이미지를 빌드하고 Kind 클러스터를 생성/배포합니다.

```bash
# 전체 시스템 배포 (이미지 빌드 -> 클러스터 생성 -> 배포)
./run.sh
```

### Step 3: 대시보드 접속
배포가 완료되면 스크립트가 대시보드 URL을 출력합니다.
*   **Access**: `http://localhost:30007` (또는 서버 Public IP:30007)

---

## 🛠️ 4. 모니터링 및 운영 (Monitoring with k9s)

Kubernetes 클러스터의 상태를 직관적으로 관리하기 위해 **k9s** 사용을 적극 권장합니다.

### k9s 실행
```bash
k9s
```

### 주요 기능 및 단축키
1.  **파드 목록 보기 (`:pods` 또는 `0`)**:
    *   실행 중인 모든 파드의 상태(`Running`, `Pending`, `Error`)를 실시간으로 확인합니다.
2.  **로그 확인 (`l`)**:
    *   파드를 선택하고 `l` (소문자 L)을 누르면 실시간 로그를 볼 수 있습니다. `inference` 파드의 로그를 통해 AI 추론 과정을 모니터링하세요.
3.  **쉘 접속 (`s`)**:
    *   파드를 선택하고 `s`를 누르면 해당 컨테이너의 쉘(Shell)로 진입합니다. 내부 파일 시스템 확인이나 네트워크 테스트(`curl`, `ping`) 시 유용합니다.
4.  **포트 포워딩 (`shift + f`)**:
    *   서비스나 파드에 직접 포트 포워딩을 설정하여 로컬에서 접근할 수 있습니다.
5.  **리소스 삭제 (`ctrl + d`)**:
    *   오동작하는 파드를 강제로 재시작하려면 선택 후 `ctrl + d`를 누릅니다.

---

## 📂 5. 프로젝트 폴더 구조 (Directory Structure)

```
safety-hanta/
├── connect.sh          # 원격지 RTSP 소스 연결을 위한 SSH 터널링 스크립트
├── run.sh              # 전체 시스템 빌드 및 배포 자동화 스크립트
├── src/                # 소스 코드 디렉토리
│   ├── capture/        # RTSP 영상 수집 및 프레임 추출
│   ├── inference/      # VLLM 기반 AI 추론 엔진
│   ├── logic/          # 추론 결과 후처리 비즈니스 로직
│   ├── notification/   # 텔레그램 알림 발송 모듈
│   └── dashboard/      # 웹 모니터링 대시보드
├── k8s/                # Kubernetes Manifest 파일 모음 (Deployment, Service, Config)
├── prompts/            # LLM/VLM용 시스템 프롬프트 및 Few-shot 예제
├── configs/            # 모델 및 시스템 설정 파일
└── videos/             # (Volume) 수집된 비디오 및 임시 데이터 저장소
```

---

## 🎥 6. RTSP 모드 전환 가이드 (RTSP Mode Switching)

개발 환경(Simulation)과 실제 현장(Real CCTV) 환경 간 전환 방법입니다.

### 6.1 시뮬레이션 모드 (Simulation Mode)
로컬에 저장된 비디오 파일을 RTSP 스트림으로 송출하여 테스트하는 모드입니다.

**1. RTSP 시뮬레이터 활성화 (`k8s/02-rtsp-sim.yaml`)**
```yaml
spec:
  replicas: 1  # 1 이상으로 로 설정하여 시뮬레이터 파드 실행 (비디오 파일은 videos/accident_video-##.mpy 형태로 저장)
```
각 rtsp sim 파드는 자신의 번호와 같은 번호를 가진 video를 송출하게 됩니다. 예를 들어, replicas를 2로 설정하면 accident_video-01, accident_video-02의 파일로 cam0, cam1 두 개의 비디오 스트림이 송출됩니다.

**2. 캡처 워커 설정 변경 (`k8s/03-capture-worker-deployment.yaml`)**
내부 서비스(`mediamtx-service`)를 바라보도록 설정합니다.
```yaml
env:
  - name: RTSP_BASE_URL
    value: "rtsp://mediamtx-service:8554/"
```

### 6.2 리얼 CCTV 모드 (Real CCTV Mode)
현장의 실제 CCTV RTSP 주소(SSH 터널링 경유)를 사용하는 모드입니다.
이 때 connect.sh가 실행되고 있어야 SSH 터널링을 통해 DP에 있는 비디오 스트림을 가져올 수 있습니다.

**1. RTSP 시뮬레이터 비활성화 (`k8s/02-rtsp-sim.yaml`)**
```yaml
spec:
  replicas: 0  # 0으로 설정하여 시뮬레이터 끄기 (리소스 절약)
```

**2. 캡처 워커 설정 변경 (`k8s/03-capture-worker-deployment.yaml`)**
호스트의 SSH 터널(`172.18.0.1:8558`)을 바라보도록 설정합니다.
```yaml
env:
  - name: RTSP_BASE_URL
    # value: "rtsp://mediamtx-service:8554/"  # 기존 값 주석 처리
    value: "rtsp://admin:hankook2580@172.18.0.1:8558/LiveChannel/"
```
> **Note**: `172.18.0.1`은 Kind 노드에서 베어메탈 호스트(SSH 터널이 열린 곳)로 접근하기 위한 Gateway IP입니다.