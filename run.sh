kind delete cluster --name safety-hanta
kind create cluster --name safety-hanta --config k8s/kind-config.yaml
kind load docker-image video-capture:latest --name safety-hanta --nodes safety-hanta-worker
kind load docker-image cosmos-reason1-server:latest --name safety-hanta --nodes safety-hanta-worker2
kubectl apply -k k8s/
