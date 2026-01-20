# 0. Delete temp files
rm -rf /videos/temp_video

# 1. Rebuild Images
docker build -t video-capture:latest -f Dockerfile.capture .
docker build -t cosmos-reason1-server:latest -f Dockerfile.server .

# 2. Delete and recreate Kind cluster
kind delete cluster --name safety-hanta
kind create cluster --name safety-hanta --config k8s/kind-config.yaml


# 3. Load Images into Kind
kind load docker-image video-capture:latest --name safety-hanta --nodes safety-hanta-worker,safety-hanta-worker3
kind load docker-image cosmos-reason1-server:latest --name safety-hanta --nodes safety-hanta-worker2

# 4. Deploy to Kind
# Create Telegram Secret
kubectl create secret generic telegram-secret --from-env-file=.env --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -k k8s/
