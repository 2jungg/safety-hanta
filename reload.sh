kubectl delete deployment capture-worker --ignore-not-found
kubectl apply -k k8s/
kubectl rollout restart deployment/inference
kubectl rollout restart statefulset/capture-worker