# Windows 개발 환경 설정 가이드 (WSL2 + Docker + Kubernetes)

이 가이드는 Windows 환경에서 WSL2를 사용하여 프로젝트를 실행하기 위한 설정 방법을 설명합니다.

## 1. WSL2 및 Ubuntu 설치

Windows에서 Linux 환경을 사용하기 위해 WSL2를 설치해야 합니다.

1.  **PowerShell(관리자 권한)**을 실행합니다.
2.  아래 명령어를 입력하여 WSL을 설치합니다.
    ```powershell
    wsl --install
    ```
    *   이미 설치되어 있다면 `wsl --update`로 최신 버전으로 업데이트하세요.
3.  설치가 완료되면 컴퓨터를 **재부팅**합니다.
4.  재부팅 후 자동으로 우분투(Ubuntu) 터미널이 열리면, 사용할 `username`과 `password`를 설정합니다.

## 2. Windows용 Docker Desktop 설치

Docker 컨테이너를 실행하기 위해 Docker Desktop을 설치하고 WSL2와 연동합니다.

1.  [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)를 다운로드하고 설치합니다.
2.  설치 중 **"Use WSL 2 instead of Hyper-V"** 옵션이 체크되어 있는지 확인합니다 (기본값).
3.  설치 완료 후 Docker Desktop을 실행합니다.
4.  **Settings (톱니바퀴) -> Resources -> WSL Integration**으로 이동합니다.
5.  "Enable integration with my default WSL distro"가 체크되어 있는지 확인하고, 아래 **"Enable integration with additional distros"**에서 설치한 `Ubuntu`를 활성화(스위치 ON)합니다.
6.  `Apply & restart`를 클릭하여 적용합니다.

## 3. NVIDIA GPU 설정 (AI 모델 실행용)

AI 모델 추론을 위해 GPU를 사용하려면 NVIDIA 드라이버 설정이 필요합니다.

1.  **Windows**에 최신 [NVIDIA GPU 드라이버](https://www.nvidia.com/Download/index.aspx)를 설치합니다.
    *   *주의: WSL2 내부에 드라이버를 설치하지 마세요. Windows에 설치하면 자동으로 연동됩니다.*
2.  설치가 잘 되었는지 확인하기 위해 **WSL2 터미널**에서 아래 명령어를 입력합니다.
    ```bash
    nvidia-smi
    ```
    GPU 정보가 출력되면 성공입니다.

## 4. 필수 도구 설치 (WSL2 내부)

이제 **WSL2 터미널(Ubuntu)**을 열고 필요한 도구들(`kubectl`, `kind`)을 설치합니다.

### 4.1 기본 패키지 업데이트
```bash
sudo apt-get update && sudo apt-get install -y curl
```

### 4.2 Kubectl 설치
Kubernetes 클러스터를 제어하기 위한 CLI 도구입니다.

```bash
# 최신 릴리즈 다운로드
curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl"

# 설치
sudo install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl

# 버전 확인
kubectl version --client
```

### 4.3 Kind (Kubernetes in Docker) 설치
로컬 Kubernetes 클러스터를 생성하는 도구입니다.

```bash
# 바이너리 다운로드
[ $(uname -m) = x86_64 ] && curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64
# 실행 권한 부여 및 이동
chmod +x ./kind
sudo mv ./kind /usr/local/bin/kind

# 설치 확인
kind --version
```

## 5. 프로젝트 실행

모든 설정이 완료되었습니다. 프로젝트를 실행합니다.

1.  프로젝트 디렉토리로 이동합니다.
    ```bash
    cd /path/to/safety-hanta
    ```
2.  실행 스크립트를 실행합니다. 이 스크립트는 이미지를 빌드하고 Kind 클러스터를 생성하여 배포합니다.
    ```bash
    chmod +x run.sh
    ./run.sh
    ```
3.  Pods 상태 확인:
    ```bash
    watch kubectl get pods
    ```

## 트러블슈팅

*   **GPU 메모리 오류 (OOM)**: `configs/vision_config.yaml`이나 `k8s/04-inference.yaml`의 메모리 설정을 확인하세요.
*   **Docker 권한 오류**: `docker ps` 입력 시 권한 오류가 나면 Docker Desktop이 실행 중인지 확인하세요. (WSL2에서는 `sudo` 없이 docker 명령어를 사용할 수 있어야 합니다).
