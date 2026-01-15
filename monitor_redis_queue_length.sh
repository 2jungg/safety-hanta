#!/bin/bash
echo "Monitoring Redis Queue Length (Ctrl+C to stop)..."
while true; do
  # Fetch queue length
  LEN=$(kubectl exec deployment/redis -- redis-cli -r 1 LLEN video_stream_queue)
  # Print with timestamp
  echo "[$(date '+%H:%M:%S')] Queue Size: $LEN"
  sleep 1
done
