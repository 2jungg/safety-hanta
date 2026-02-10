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

echo "Waiting for dashboard to be ready..."
kubectl wait --for=condition=ready pod -l app=dashboard --timeout=300s

echo "----------------------------------------------------------------"
echo "Dashboard is ready!"
echo "Access it here: http://202.31.34.240:30007"
echo "----------------------------------------------------------------"

# Check and kill process on port 30007
PID=$(lsof -ti :30007)
if [ -n "$PID" ]; then
  echo "Port 30007 is in use by PID $PID. Killing it..."
  kill -9 $PID
fi

# 대쉬보드 출력을 위한 포트 포워딩
kubectl port-forward --address 0.0.0.0 svc/dashboard-service 30007:80