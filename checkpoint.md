1. Device Plugin 오류 (Memory Not Supported)
원인: 기존 플러그인 버전(v0.17.1)이 최신 GPU 아키텍처나 드라이버 정보를 제대로 읽지 못했어.
해결: v0.17.4 버전으로 업그레이드해서 해결했어.
2. Inference Pod 오류 (CDI Injection Failed)
원인: runtimeClassName: nvidia를 쓰면 **NVIDIA Runtime (CDI)**이 GPU를 주입해줘야 하는데, Kind 환경 특성상 이 과정이 계속 꼬였어. (그래서 failed to inject CDI devices 에러 발생)
해결: 과감하게 **NVIDIA Runtime을 쓰지 않는 방식(Bypass)**으로 우회했어.
3. VLLM 오류 (Device string must not be empty)
원인: Runtime을 껐더니, Pod 안에는 GPU 장치 파일(/dev/nvidia0)도 없고 드라이버(libcuda.so)도 없어서 앱이 GPU를 못 찾았어.
해결 (핵심!): "수동 주입" 전략을 썼어.
Device Plugin 설정: PASS_DEVICE_SPECS="true"를 켜서, 플러그인이 런타임 없이도 직접 장치 파일(/dev/nvidia*)을 Pod에 꽂아주게 만듦.
Pod 설정: 호스트에 있는 드라이버 파일들(libcuda.so 등)을 **hostPath**로 직접 마운트하고, **LD_LIBRARY_PATH**를 잡아줘서 앱이 이걸 로컬 드라이버처럼 쓰게 속임.
