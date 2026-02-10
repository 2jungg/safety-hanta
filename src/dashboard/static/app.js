// DOM Elements
const cameraList = document.getElementById('camera-list');
const eventFeed = document.getElementById('event-feed');
const mainPlayer = document.getElementById('main-player');
const videoTitle = document.getElementById('video-title');
const videoDesc = document.getElementById('video-desc');
const playerContainer = document.getElementById('player-container');
const emergencyOverlay = document.getElementById('emergency-overlay');
const activeCamCount = document.getElementById('active-cam-count');
const emptyState = document.getElementById('empty-state');
const videoSource = document.getElementById('video-source');
const liveBadge = document.getElementById('live-badge');

// State
let cameras = new Set();
let onlineCameras = new Set();
let lastEventTime = 0;
let isEmergency = false;

// Modal Elements
const modalOverlay = document.getElementById('modal-overlay');
const modalPlayer = document.getElementById('modal-player');
const modalSource = document.getElementById('modal-source');
const modalTitle = document.getElementById('modal-title');
const modalDesc = document.getElementById('modal-desc');
const modalMeta = document.getElementById('modal-meta');
const modalLog = document.getElementById('modal-log');
const modalClose = document.getElementById('modal-close');

// Close Modal Logic
modalClose.onclick = () => closeModal();
modalOverlay.onclick = (e) => {
    if (e.target === modalOverlay) closeModal();
};

function closeModal() {
    modalOverlay.style.display = 'none';
    modalPlayer.pause();
    modalSource.src = "";
}

// Format Utils
const formatTime = (ts) => new Date(ts * 1000).toLocaleTimeString();

// --- API Calls ---

async function fetchStatus() {
    try {
        const res = await fetch('/api/status');
        const data = await res.json();

        // Update Online Cameras
        onlineCameras.clear();
        if (data.online) {
            data.online.forEach(cam => onlineCameras.add(cam));
        }

        // Update Camera List UI
        renderCameras();

        // Update Header Count
        activeCamCount.innerText = onlineCameras.size;

    } catch (e) {
        console.error("Status fetch error:", e);
    }
}

async function fetchEvents() {
    try {
        const res = await fetch('/api/events');
        const events = await res.json();

        // If no events, stop here
        if (!events || events.length === 0) return;

        // Check for new high-priority events
        const latestEvent = events[0]; // List is LIFO (newest first)
        const latestTs = latestEvent.timestamp;

        if (latestTs > lastEventTime) {
            lastEventTime = latestTs;

            // Only rebuild feed if there are new events to avoid flicker
            renderFeed(events);

            // Auto-play if DANGER/EXTREME
            if (latestEvent.level === 'DANGER' || latestEvent.level === 'EXTREME') {
                triggerEmergency(latestEvent);
            }
        }
    } catch (e) {
        console.error("Events fetch error:", e);
    }
}

// --- Rendering ---

function renderCameras() {
    const allCams = new Set([...cameras, ...onlineCameras]);
    const sortedCams = Array.from(allCams).sort();

    cameraList.innerHTML = '';

    sortedCams.forEach(cam => {
        const isOnline = onlineCameras.has(cam);

        const el = document.createElement('div');
        el.className = `p-3 rounded-lg flex items-center justify-between transition cursor-pointer border ${isOnline ? 'bg-slate-800/80 border-slate-700 hover:bg-slate-700' : 'bg-slate-900/50 border-transparent opacity-60'
            }`;

        el.innerHTML = `
            <div class="flex items-center space-x-3">
                <div class="status-dot ${isOnline ? 'online animate-pulse-green' : 'offline'}"></div>
                <span class="font-mono text-sm font-semibold text-gray-200">${cam}</span>
            </div>
            <span class="text-xs ${isOnline ? 'text-green-400' : 'text-gray-500'}">
                ${isOnline ? 'LIVE' : 'OFF'}
            </span>
        `;

        // Click to view Live Stream
        el.onclick = () => playLiveStream(cam);

        cameraList.appendChild(el);
    });
}

function renderFeed(events) {
    eventFeed.innerHTML = '';

    events.forEach(evt => {
        // Track cameras found in events
        if (!cameras.has(evt.stream_id)) {
            cameras.add(evt.stream_id);
        }

        const card = document.createElement('div');
        card.className = `event-card p-4 rounded-lg cursor-pointer mb-3 relative overflow-hidden ${evt.level}`;

        card.innerHTML = `
            <div class="flex justify-between items-start mb-1">
                <span class="font-bold font-mono ${getLevelColor(evt.level)}">${evt.level}</span>
                <span class="text-xs text-gray-400 font-mono">${formatTime(evt.timestamp)}</span>
            </div>
            <div class="font-semibold text-sm text-gray-200 mb-1">📷 ${evt.stream_id}</div>
            <div class="text-xs text-gray-400 line-clamp-2">${evt.description}</div>
        `;

        // CLICK EVENT -> Open Modal
        card.onclick = () => openAlertModal(evt);
        eventFeed.appendChild(card);
    });
}

function getLevelColor(level) {
    switch (level) {
        case 'DANGER': return 'text-red-500';
        case 'EXTREME': return 'text-red-500';
        case 'WARN': return 'text-amber-400';
        case 'SAFE': return 'text-green-500';
        default: return 'text-gray-400';
    }
}

// --- Actions ---

function playLiveStream(streamId) {
    // Hide empty state
    emptyState.classList.add('hidden');
    document.getElementById('player-container').classList.remove('hidden');

    // Set Main Player to Live MJPEG Stream
    // Note: Use img tag for MJPEG if video tag fails, but usually browsers handle MJPEG via direct nav or img.
    // Actually, for MJPEG via API, 'video' tag src might not work everywhere. Best to use <img> for MJPEG.
    // Let's swap video tag for img tag dynamically or just separate containers?
    // Quick fix: Just set src. If it's chrome it handles it.
    // BETTER: Use an <img> element for live stream to act as a video player.

    // Check if we have an img player, if not replace
    let player = document.getElementById('main-player');
    const container = document.getElementById('player-container');

    // Remove existing player content
    container.innerHTML = `
        <img id="live-player" src="/api/live/${streamId}" class="w-full h-full object-contain">
        <div class="absolute top-4 left-4 bg-green-600 text-white px-3 py-1 rounded font-bold font-mono animate-pulse">
            LIVE FEED
        </div>
        <div class="scanner-overlay opacity-30 group-hover:opacity-10 transition"></div>
    `;

    // Update Info Panel
    videoTitle.innerText = `LIVE MONITORING: ${streamId}`;
    videoTitle.className = `text-xl font-bold text-green-400 font-mono`;
    videoDesc.innerText = `Real-time RTSP Feed via Proxy`;
}

function openAlertModal(evt) {
    modalOverlay.style.display = 'flex';

    const filename = evt.video_clip.split('/').pop();
    const url = `/video/${filename}`;

    modalSource.src = url;
    modalPlayer.load();
    modalPlayer.play();

    modalTitle.innerText = `${evt.level} DETECTED`;
    modalTitle.className = `text-2xl font-bold font-mono mb-2 ${getLevelColor(evt.level)}`;
    modalMeta.innerText = `TIME: ${formatTime(evt.timestamp)} | CAM: ${evt.stream_id}`;
    modalDesc.innerText = evt.description;
    modalLog.innerText = evt.full_analysis || "No details available.";
}

function triggerEmergency(evt) {
    // Flash Overlay
    emergencyOverlay.style.display = 'block';

    // Auto Open Modal
    openAlertModal(evt);

    // Stop flash after 5s
    setTimeout(() => {
        emergencyOverlay.style.display = 'none';
    }, 5000);
}

// --- Init ---

// Clock
setInterval(() => {
    document.getElementById('clock').innerText = new Date().toLocaleTimeString();
}, 1000);

// Polling
fetchStatus();
fetchEvents();

setInterval(fetchStatus, 3000); // Check online status every 3s
setInterval(fetchEvents, 2000); // Check events every 2s

// Initial Render
renderCameras();
