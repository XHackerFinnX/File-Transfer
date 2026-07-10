/*
 * Production-ready 1-to-1 WebRTC calls for the encrypted chat.
 * Requires window.currentChatPeer and the existing WebSocket signaling channel.
 */
(() => {
    "use strict";

    const DEFAULT_STUN_SERVERS = [
        { urls: "stun:stun.l.google.com:19302" },
        { urls: "stun:stun1.l.google.com:19302" },
        { urls: "stun:stun.cloudflare.com:3478" },
    ];

    const TURN_ENDPOINTS = ["/turn-credentials", "/chat/turn-credentials"];
    const CALL_ANSWER_TIMEOUT_MS = 45_000;
    const CALL_CONNECT_TIMEOUT_MS = 25_000;
    const MAX_ICE_RESTARTS = 2;

    const icons = {
        phone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.11 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.12.9.33 1.77.63 2.61a2 2 0 0 1-.45 2.11L8.09 9.64a16 16 0 0 0 6.27 6.27l1.2-1.2a2 2 0 0 1 2.11-.45c.84.3 1.71.51 2.61.63A2 2 0 0 1 22 16.92z"/></svg>',
        phoneOff:
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.7 13.3a16 16 0 0 0 3 3l1.2-1.2a2 2 0 0 1 2.1-.45c.84.3 1.71.51 2.61.63A2 2 0 0 1 21.33 17v3a2 2 0 0 1-2.18 2A19.8 19.8 0 0 1 3.02 5.85 2 2 0 0 1 5 3.67h3a2 2 0 0 1 1.7 1.72c.12.9.33 1.77.63 2.61a2 2 0 0 1-.45 2.11l-1.2 1.2z"/><path d="M22 2 2 22"/></svg>',
        mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3"/></svg>',
        micOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 9v3a3 3 0 0 0 5.12 2.12M15 9V5a3 3 0 0 0-5.94-.6M5 10v2a7 7 0 0 0 11.72 5.15M19 10v2a7 7 0 0 1-.33 2.12M12 19v3M2 2l20 20"/></svg>',
        camera: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="5" width="14" height="14" rx="3"/><path d="m16 10 6-3v10l-6-3z"/></svg>',
        cameraOff:
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.7 5H5a3 3 0 0 0-3 3v8a3 3 0 0 0 3 3h11V10.3M16 10l6-3v10l-4.2-2.1M2 2l20 20"/></svg>',
        screen: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4M8 11l4-4 4 4M12 7v7"/></svg>',
        settings:
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.33 1.82H9.67A1.7 1.7 0 0 0 9 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1.82-.33V9.67A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .33-1.82h4A1.7 1.7 0 0 0 15 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9c.18.31.4.65.6 1 .29.5.97.74 1.82.33v4A1.7 1.7 0 0 0 19.4 15z"/></svg>',
        chevron:
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="m9 18 6-6-6-6"/></svg>',
        volume: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 5 6 9H2v6h4l5 4zM15.5 8.5a5 5 0 0 1 0 7M18 6a8 8 0 0 1 0 12"/></svg>',
        shield: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><path d="m9 12 2 2 4-4"/></svg>',
    };

    const state = {
        pc: null,
        peerId: null,
        peerName: "Собеседник",
        inCall: false,
        isInitiator: false,
        phase: "idle",
        localStream: null,
        rawMicStream: null,
        cameraStream: null,
        screenStream: null,
        remoteStream: null,
        remoteAudioStream: null,
        remoteVideoStream: null,
        audioContext: null,
        audioDestination: null,
        micGain: null,
        screenGain: null,
        audioSender: null,
        videoSender: null,
        micEnabled: true,
        cameraEnabled: false,
        screenEnabled: false,
        noiseSuppression: true,
        micVolume: 1,
        screenVolume: 0.8,
        speakerVolume: 1,
        selectedMicId: "",
        selectedCameraId: "",
        selectedSpeakerId: "",
        pendingCandidates: [],
        pendingSignals: [],
        preparingPromise: null,
        turnAvailable: false,
        turnRelayDetected: false,
        iceRestartCount: 0,
        answerTimer: null,
        connectTimer: null,
        durationTimer: null,
        callStartedAt: 0,
        meters: [],
        deviceChangeHandler: null,
        remoteMeterStarted: false,
        localMeterStarted: false,
        audioUnlocked: false,
    };

    const $ = (id) => document.getElementById(id);
    const safe = (value) =>
        window.escapeHtml
            ? window.escapeHtml(String(value ?? ""))
            : String(value ?? "").replace(
                  /[&<>"']/g,
                  (char) =>
                      ({
                          "&": "&amp;",
                          "<": "&lt;",
                          ">": "&gt;",
                          '"': "&quot;",
                          "'": "&#39;",
                      })[char],
              );
    const safeAttr = (value) =>
        window.escapeAttr
            ? window.escapeAttr(String(value ?? ""))
            : safe(value);
    const initial = (name) =>
        String(name || "?")
            .trim()
            .slice(0, 1)
            .toUpperCase() || "?";
    const callPeer = () =>
        window.currentChatPeer || {
            id: window.peerId,
            nickname: window.peerNickname,
        };

    function send(type, data = {}) {
        const payload = { ...data };
        if (state.peerId && !payload.to) payload.to = state.peerId;
        if (typeof window.sendWsMessage === "function") {
            return window.sendWsMessage(type, payload);
        }
        if (!window.ws || window.ws.readyState !== WebSocket.OPEN) return false;
        window.ws.send(JSON.stringify({ type, data: payload }));
        return true;
    }

    function toast(title, text = "", variant = "info") {
        const node = document.createElement("div");
        node.className = `call-toast call-toast--${variant}`;
        node.innerHTML = `
            <div class="call-toast__icon">${variant === "danger" ? icons.phoneOff : icons.shield}</div>
            <div class="call-toast__body">
                <div class="call-toast__title">${safe(title)}</div>
                ${text ? `<div class="call-toast__text">${safe(text)}</div>` : ""}
            </div>`;
        document.body.appendChild(node);
        requestAnimationFrame(() => node.classList.add("is-visible"));
        setTimeout(() => {
            node.classList.remove("is-visible");
            setTimeout(() => node.remove(), 250);
        }, 4200);
    }

    function addChatSystemMessage(text) {
        if (typeof window.addSystemMessage === "function") {
            window.addSystemMessage(text);
        } else {
            toast("Звонок", text);
        }
    }

    function assertMediaSupport() {
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
            const error = new Error(
                "Камера и микрофон доступны только по HTTPS или на localhost.",
            );
            error.code = "INSECURE_CONTEXT";
            throw error;
        }
        if (typeof RTCPeerConnection === "undefined") {
            throw new Error("Этот браузер не поддерживает WebRTC.");
        }
    }

    function setBodyCallMode(enabled) {
        document.body.classList.toggle("call-is-open", enabled);
    }

    function createIncomingModal(fromName, fromId) {
        document
            .querySelectorAll(".incoming-call-overlay")
            .forEach((node) => node.remove());
        const modal = document.createElement("div");
        modal.className = "incoming-call-overlay";
        modal.innerHTML = `
            <div class="incoming-call-card" role="dialog" aria-modal="true" aria-label="Входящий звонок">
                <div class="incoming-call-card__glow"></div>
                <div class="incoming-call-card__badge">Входящий звонок</div>
                <div class="incoming-call-avatar-wrap">
                    <div class="incoming-call-avatar-ring"></div>
                    <div class="incoming-call-avatar">${safe(initial(fromName))}</div>
                </div>
                <h2 class="incoming-call-name">${safe(fromName)}</h2>
                <p class="incoming-call-text">Голосовой вызов в защищённом P2P-чате</p>
                <div class="incoming-call-actions">
                    <button class="incoming-call-action incoming-call-action--reject" id="callRejectIncoming" type="button">
                        <span>${icons.phoneOff}</span><b>Отклонить</b>
                    </button>
                    <button class="incoming-call-action incoming-call-action--accept" id="callAcceptIncoming" type="button">
                        <span>${icons.phone}</span><b>Ответить</b>
                    </button>
                </div>
            </div>`;
        document.body.appendChild(modal);

        $("callRejectIncoming").onclick = () => {
            modal.remove();
            send("call_response", { to: fromId, accepted: false });
            addChatSystemMessage("Вы отклонили входящий звонок.");
        };

        $("callAcceptIncoming").onclick = async () => {
            const button = $("callAcceptIncoming");
            button.disabled = true;
            button.classList.add("is-loading");
            state.peerId = fromId;
            state.peerName = fromName || "Собеседник";
            state.isInitiator = false;
            modal.remove();
            try {
                await prepareCall({ phase: "connecting" });
                send("call_response", { to: fromId, accepted: true });
                await flushPendingSignals();
            } catch (error) {
                console.error("[CALL] Unable to accept call", error);
                send("call_response", { to: fromId, accepted: false });
                cleanupActiveCall(false);
                toast("Не удалось ответить", mediaErrorText(error), "danger");
            }
        };
    }

    function renderCallOverlay() {
        document
            .querySelectorAll(".call-overlay")
            .forEach((node) => node.remove());
        const node = document.createElement("section");
        node.className = "call-overlay";
        node.id = "callOverlay";
        node.innerHTML = `
            <div class="call-ambient call-ambient--one"></div>
            <div class="call-ambient call-ambient--two"></div>

            <header class="call-topbar">
                <div class="call-topbar__identity">
                    <div class="call-brand-mark">${icons.phone}</div>
                    <div class="call-topbar__copy">
                        <div class="call-title">Звонок с ${safe(state.peerName)}</div>
                        <div class="call-subtitle"><span class="call-secure-dot"></span><span id="callSubtitle">Подготовка защищённого соединения</span></div>
                    </div>
                </div>
                <div class="call-topbar__status">
                    <span class="call-status-pill" id="callStatusPill">Подключение</span>
                    <span class="call-duration" id="callDuration">00:00</span>
                </div>
            </header>

            <main class="call-stage">
                <div class="call-media-layout">
                    ${tileHtml("remote", state.peerName, false)}
                    ${tileHtml("local", "Вы", true)}
                    <button class="call-audio-unlock" id="callAudioUnlock" type="button" hidden>
                        ${icons.volume}<span>Нажмите, чтобы включить звук собеседника</span>
                    </button>
                    <div class="call-connection-banner" id="callConnectionBanner">
                        <span class="call-connection-spinner"></span>
                        <span id="callConnectionText">Подготавливаем микрофон и сетевой маршрут…</span>
                    </div>
                </div>

                <aside class="call-sidepanel" id="callSidepanel" aria-hidden="true">
                    <div class="call-sidepanel__header">
                        <div><span>Настройки</span><small>Устройства и качество</small></div>
                        <button class="call-panel-close" id="callSettingsClose" type="button" aria-label="Закрыть настройки">×</button>
                    </div>
                    <div class="call-sidepanel__content">
                        <section class="call-settings-card">
                            <div class="call-settings-card__heading"><span class="call-settings-icon">${icons.mic}</span><div><b>Микрофон</b><small id="callMicPermissionStatus">Разрешение ещё не получено</small></div></div>
                            <label class="call-label" for="callMicSelect">Устройство ввода</label>
                            <select class="call-select" id="callMicSelect"></select>
                            <label class="call-range-label" for="callMicVolume"><span>Громкость микрофона</span><b id="callMicVolumeText">100%</b></label>
                            <input class="call-range" id="callMicVolume" type="range" min="0" max="200" value="100">
                            <div class="call-meter"><div class="call-meter-fill" id="callMicMeter"></div></div>
                            <label class="call-switch-row"><span>Шумоподавление</span><input id="callNoiseSuppression" type="checkbox" checked><i></i></label>
                        </section>

                        <section class="call-settings-card">
                            <div class="call-settings-card__heading"><span class="call-settings-icon">${icons.volume}</span><div><b>Динамики</b><small id="callSpeakerPermissionStatus">Системное устройство по умолчанию</small></div></div>
                            <label class="call-label" for="callSpeakerSelect">Устройство вывода</label>
                            <select class="call-select" id="callSpeakerSelect"></select>
                            <label class="call-range-label" for="callSpeakerVolume"><span>Громкость</span><b id="callSpeakerVolumeText">100%</b></label>
                            <input class="call-range" id="callSpeakerVolume" type="range" min="0" max="100" value="100">
                            <div class="call-settings-actions">
                                <button class="call-mini-btn" id="callPickSpeaker" type="button">Выбрать</button>
                                <button class="call-mini-btn" id="callTestSpeaker" type="button">Проверить</button>
                            </div>
                        </section>

                        <section class="call-settings-card">
                            <div class="call-settings-card__heading"><span class="call-settings-icon">${icons.camera}</span><div><b>Камера</b><small id="callCameraPermissionStatus">Камера выключена</small></div></div>
                            <label class="call-label" for="callCameraSelect">Устройство камеры</label>
                            <select class="call-select" id="callCameraSelect"></select>
                            <button class="call-mini-btn call-mini-btn--wide" id="callTestCamera" type="button">Включить и проверить камеру</button>
                        </section>

                        <section class="call-settings-card call-diagnostics">
                            <div class="call-settings-card__heading"><span class="call-settings-icon">${icons.shield}</span><div><b>Соединение</b><small id="callTransportText">Определяем маршрут…</small></div></div>
                            <div class="call-diagnostic-row"><span>WebRTC</span><b id="callWebRtcState">new</b></div>
                            <div class="call-diagnostic-row"><span>ICE</span><b id="callIceState">new</b></div>
                            <div class="call-diagnostic-row"><span>TURN</span><b id="callTurnState">проверяется</b></div>
                            <button class="call-mini-btn call-mini-btn--wide" id="callRefreshDevices" type="button">Обновить устройства</button>
                        </section>
                    </div>
                </aside>
            </main>

            <footer class="call-controls">
                ${controlButton("callMicToggle", "active", icons.mic, "Микрофон")}
                ${controlButton("callCameraToggle", "", icons.cameraOff, "Камера")}
                ${controlButton("callScreenToggle", "", icons.screen, "Экран")}
                ${controlButton("callSettingsToggle", "", icons.settings, "Настройки")}
                ${controlButton("callEndButton", "end", icons.phoneOff, "Завершить")}
            </footer>`;
        document.body.appendChild(node);
        setBodyCallMode(true);
    }

    function controlButton(id, className, icon, label) {
        return `<button class="call-control ${className}" id="${id}" type="button"><span class="call-control__icon">${icon}</span><span class="call-control__label">${label}</span></button>`;
    }

    function tileHtml(kind, name, muted) {
        const isLocal = kind === "local";
        return `<article class="call-tile call-tile--${kind}" id="${kind}CallTile">
            <video id="${kind}CallVideo" ${muted ? "muted" : ""} autoplay playsinline class="call-video is-hidden"></video>
            ${kind === "remote" ? '<audio id="remoteCallAudio" autoplay playsinline></audio>' : ""}
            <div class="call-empty-camera" id="${kind}CallEmpty">
                <div class="call-avatar-orbit"><span></span><span></span><div class="call-avatar">${safe(initial(name))}</div></div>
                <h3>${safe(name)}</h3>
                <p>${isLocal ? "Камера выключена" : "Ожидаем подключение медиа"}</p>
            </div>
            <div class="call-tile-footer">
                <div class="call-person-meta"><b>${safe(name)}</b><span id="${kind}CallState">${isLocal ? "Микрофон включён" : "Подключение…"}</span></div>
                <div class="call-speaking-indicator"><i></i><i></i><i></i></div>
            </div>
        </article>`;
    }

    async function prepareCall({ phase = "connecting" } = {}) {
        if (state.preparingPromise) return state.preparingPromise;
        state.preparingPromise = (async () => {
            assertMediaSupport();
            state.inCall = true;
            state.phase = phase;
            renderCallOverlay();
            bindControls();
            updatePhase(phase);
            await loadDevices();
            await ensureMicrophone();
            await loadDevices();
            await createPeerConnection();
            subscribeDeviceChanges();
            startLocalMeter();
            armConnectTimeout();
        })();

        try {
            await state.preparingPromise;
        } finally {
            state.preparingPromise = null;
        }
    }

    function updatePhase(phase, detail = "") {
        state.phase = phase;
        const pill = $("callStatusPill");
        const subtitle = $("callSubtitle");
        const banner = $("callConnectionBanner");
        const text = $("callConnectionText");
        const phaseData = {
            ringing: ["Звоним…", "Ожидаем ответ собеседника"],
            connecting: ["Подключение", "Устанавливаем защищённый медиаканал"],
            connected: ["В звонке", "Защищённый WebRTC-канал активен"],
            reconnecting: ["Восстановление", "Перестраиваем сетевой маршрут"],
            failed: ["Нет связи", "Не удалось установить медиаканал"],
        };
        const [label, description] = phaseData[phase] || [
            "Звонок",
            "Подготовка соединения",
        ];
        if (pill) {
            pill.textContent = label;
            pill.dataset.state = phase;
        }
        if (subtitle) subtitle.textContent = detail || description;
        if (text) text.textContent = detail || description;
        if (banner) banner.classList.toggle("is-hidden", phase === "connected");
    }

    function bindControls() {
        $("callMicToggle").onclick = toggleMic;
        $("callCameraToggle").onclick = () => void toggleCamera();
        $("callScreenToggle").onclick = () => void toggleScreen();
        $("callSettingsToggle").onclick = toggleSettings;
        $("callSettingsClose").onclick = closeSettings;
        $("callEndButton").onclick = () => endCall(true, "Звонок завершён");
        $("callAudioUnlock").onclick = () => void unlockRemoteAudio();

        $("callMicVolume").oninput = (event) => {
            state.micVolume = Number(event.target.value) / 100;
            $("callMicVolumeText").textContent = `${event.target.value}%`;
            if (state.micGain) state.micGain.gain.value = state.micVolume;
        };
        $("callSpeakerVolume").oninput = (event) => {
            state.speakerVolume = Number(event.target.value) / 100;
            $("callSpeakerVolumeText").textContent = `${event.target.value}%`;
            const remoteAudio = $("remoteCallAudio");
            if (remoteAudio) remoteAudio.volume = state.speakerVolume;
        };
        $("callNoiseSuppression").onchange = async (event) => {
            state.noiseSuppression = event.target.checked;
            await ensureMicrophone({ force: true });
        };
        $("callMicSelect").onchange = async (event) => {
            state.selectedMicId = event.target.value;
            await ensureMicrophone({ force: true });
        };
        $("callCameraSelect").onchange = async (event) => {
            state.selectedCameraId = event.target.value;
            if (state.cameraEnabled) await startCamera({ force: true });
        };
        $("callSpeakerSelect").onchange = async (event) => {
            state.selectedSpeakerId = event.target.value;
            await setSpeakerSink();
        };
        $("callPickSpeaker").onclick = () => void pickSpeakerOutput();
        $("callTestSpeaker").onclick = testSpeaker;
        $("callTestCamera").onclick = () => void toggleCamera(true);
        $("callRefreshDevices").onclick = async () => {
            await loadDevices();
            toast(
                "Устройства обновлены",
                "Список камер и аудиоустройств перечитан.",
                "success",
            );
        };
    }

    function toggleSettings() {
        const panel = $("callSidepanel");
        if (!panel) return;
        const open = !panel.classList.contains("is-open");
        panel.classList.toggle("is-open", open);
        panel.setAttribute("aria-hidden", String(!open));
        $("callSettingsToggle")?.classList.toggle("active", open);
    }

    function closeSettings() {
        const panel = $("callSidepanel");
        panel?.classList.remove("is-open");
        panel?.setAttribute("aria-hidden", "true");
        $("callSettingsToggle")?.classList.remove("active");
    }

    function audioConstraints() {
        const deviceId = $("callMicSelect")?.value || state.selectedMicId;
        return {
            deviceId: deviceId ? { exact: deviceId } : undefined,
            echoCancellation: true,
            noiseSuppression: state.noiseSuppression,
            autoGainControl: true,
            channelCount: { ideal: 1 },
            sampleRate: { ideal: 48_000 },
        };
    }

    function videoConstraints() {
        const deviceId = $("callCameraSelect")?.value || state.selectedCameraId;
        return {
            deviceId: deviceId ? { exact: deviceId } : undefined,
            width: { ideal: 1280 },
            height: { ideal: 720 },
            frameRate: { ideal: 30, max: 30 },
        };
    }

    async function getUserMediaWithFallback(constraints, kind) {
        try {
            return await navigator.mediaDevices.getUserMedia(constraints);
        } catch (error) {
            if (
                error?.name === "OverconstrainedError" &&
                constraints[kind]?.deviceId
            ) {
                const fallback = {
                    ...constraints,
                    [kind]: { ...constraints[kind], deviceId: undefined },
                };
                return navigator.mediaDevices.getUserMedia(fallback);
            }
            throw error;
        }
    }

    async function ensureMicrophone({ force = false } = {}) {
        if (
            !force &&
            state.rawMicStream
                ?.getAudioTracks()
                .some((track) => track.readyState === "live")
        ) {
            return;
        }
        try {
            const stream = await getUserMediaWithFallback(
                { audio: audioConstraints(), video: false },
                "audio",
            );
            rememberSelectedDevice("audioinput", stream.getAudioTracks()[0]);
            stopTracks(state.rawMicStream);
            state.rawMicStream = stream;
            await rebuildOutgoingAudio();
            const track = stream.getAudioTracks()[0];
            setDeviceHint(
                "callMicPermissionStatus",
                track?.label || "Микрофон по умолчанию",
                false,
            );
            setLocalStateText(
                state.micEnabled ? "Микрофон включён" : "Микрофон выключен",
            );
        } catch (error) {
            console.error("[CALL] Microphone unavailable", error);
            setDeviceHint(
                "callMicPermissionStatus",
                mediaErrorText(error, "микрофону"),
                true,
            );
            toast(
                "Микрофон недоступен",
                mediaErrorText(error, "микрофону"),
                "danger",
            );
            if (!state.localStream) state.localStream = new MediaStream();
        }
    }

    async function rebuildOutgoingAudio() {
        const rawMicTrack = state.rawMicStream?.getAudioTracks()[0] || null;
        const screenAudioTrack =
            state.screenStream?.getAudioTracks()[0] || null;
        const oldOutgoingTrack = state.localStream?.getAudioTracks()[0] || null;

        if (state.audioContext) {
            try {
                await state.audioContext.close();
            } catch (_) {}
        }
        state.audioContext = null;
        state.audioDestination = null;
        state.micGain = null;
        state.screenGain = null;

        let outgoingTrack = rawMicTrack;
        if (
            (rawMicTrack || screenAudioTrack) &&
            (window.AudioContext || window.webkitAudioContext)
        ) {
            try {
                const AudioContextClass =
                    window.AudioContext || window.webkitAudioContext;
                const context = new AudioContextClass();
                await context.resume().catch(() => {});
                const destination = context.createMediaStreamDestination();

                if (rawMicTrack) {
                    const micSource = context.createMediaStreamSource(
                        new MediaStream([rawMicTrack]),
                    );
                    const micGain = context.createGain();
                    micGain.gain.value = state.micEnabled ? state.micVolume : 0;
                    micSource.connect(micGain).connect(destination);
                    state.micGain = micGain;
                }
                if (screenAudioTrack) {
                    const screenSource = context.createMediaStreamSource(
                        new MediaStream([screenAudioTrack]),
                    );
                    const screenGain = context.createGain();
                    screenGain.gain.value = state.screenVolume;
                    screenSource.connect(screenGain).connect(destination);
                    state.screenGain = screenGain;
                }

                state.audioContext = context;
                state.audioDestination = destination;
                outgoingTrack =
                    destination.stream.getAudioTracks()[0] || rawMicTrack;
            } catch (error) {
                console.warn(
                    "[CALL] Audio mixer unavailable; raw microphone will be used",
                    error,
                );
                outgoingTrack = rawMicTrack || screenAudioTrack;
            }
        }

        if (!state.localStream) state.localStream = new MediaStream();
        state.localStream
            .getAudioTracks()
            .forEach((track) => state.localStream.removeTrack(track));
        if (outgoingTrack) {
            outgoingTrack.enabled =
                state.micEnabled || Boolean(screenAudioTrack);
            state.localStream.addTrack(outgoingTrack);
        }
        if (
            oldOutgoingTrack &&
            oldOutgoingTrack !== rawMicTrack &&
            oldOutgoingTrack !== outgoingTrack
        ) {
            oldOutgoingTrack.stop();
        }
        await replaceTrack("audio", outgoingTrack);
    }

    async function startCamera({ force = false } = {}) {
        if (
            !force &&
            state.cameraStream
                ?.getVideoTracks()
                .some((track) => track.readyState === "live")
        ) {
            return;
        }
        try {
            const stream = await getUserMediaWithFallback(
                { video: videoConstraints(), audio: false },
                "video",
            );
            rememberSelectedDevice("videoinput", stream.getVideoTracks()[0]);
            stopTracks(state.cameraStream);
            state.cameraStream = stream;
            state.cameraEnabled = true;
            if (!state.screenEnabled) {
                const track = stream.getVideoTracks()[0];
                await replaceTrack("video", track);
                setVideo("localCallVideo", new MediaStream([track]), true);
                setTileVideoState("local", true);
            }
            setDeviceHint(
                "callCameraPermissionStatus",
                stream.getVideoTracks()[0]?.label || "Камера включена",
                false,
            );
            updateControlVisuals();
            broadcastState();
        } catch (error) {
            state.cameraEnabled = false;
            updateControlVisuals();
            setDeviceHint(
                "callCameraPermissionStatus",
                mediaErrorText(error, "камере"),
                true,
            );
            toast(
                "Камера недоступна",
                mediaErrorText(error, "камере"),
                "danger",
            );
            throw error;
        }
    }

    async function stopCamera() {
        state.cameraEnabled = false;
        stopTracks(state.cameraStream);
        state.cameraStream = null;
        if (!state.screenEnabled) {
            await replaceTrack("video", null);
            setTileVideoState("local", false);
            setLocalStateText(
                state.micEnabled ? "Микрофон включён" : "Микрофон выключен",
            );
        }
        setDeviceHint("callCameraPermissionStatus", "Камера выключена", false);
        updateControlVisuals();
        broadcastState();
    }

    async function toggleCamera(forceOn = false) {
        if (forceOn && state.cameraEnabled) return;
        if (state.cameraEnabled && !forceOn) {
            await stopCamera();
            return;
        }
        await startCamera({ force: true });
    }

    async function toggleScreen() {
        if (state.screenEnabled) {
            await stopScreenShare();
            return;
        }
        if (!navigator.mediaDevices?.getDisplayMedia) {
            toast(
                "Демонстрация недоступна",
                "Браузер не поддерживает захват экрана.",
                "danger",
            );
            return;
        }
        try {
            const stream = await navigator.mediaDevices.getDisplayMedia({
                video: {
                    frameRate: { ideal: 30, max: 30 },
                    width: { ideal: 1920 },
                    height: { ideal: 1080 },
                },
                audio: true,
                preferCurrentTab: false,
                selfBrowserSurface: "exclude",
                surfaceSwitching: "include",
                systemAudio: "include",
            });
            state.screenStream = stream;
            state.screenEnabled = true;
            const videoTrack = stream.getVideoTracks()[0];
            videoTrack.onended = () =>
                void stopScreenShare({ fromEnded: true });
            await replaceTrack("video", videoTrack);
            await rebuildOutgoingAudio();
            setVideo("localCallVideo", new MediaStream([videoTrack]), true);
            setTileVideoState("local", true);
            setLocalStateText(
                stream.getAudioTracks().length
                    ? "Экран и звук демонстрируются"
                    : "Экран демонстрируется",
            );
            updateControlVisuals();
            broadcastState();
        } catch (error) {
            if (
                error?.name !== "NotAllowedError" &&
                error?.name !== "AbortError"
            ) {
                console.error("[CALL] Screen share failed", error);
                toast(
                    "Не удалось показать экран",
                    mediaErrorText(error, "экрану"),
                    "danger",
                );
            }
        }
    }

    async function stopScreenShare({ fromEnded = false } = {}) {
        if (!state.screenEnabled && !state.screenStream) return;
        state.screenEnabled = false;
        const stream = state.screenStream;
        state.screenStream = null;
        stopTracks(stream);
        await rebuildOutgoingAudio();
        if (state.cameraEnabled && state.cameraStream?.getVideoTracks()[0]) {
            const cameraTrack = state.cameraStream.getVideoTracks()[0];
            await replaceTrack("video", cameraTrack);
            setVideo("localCallVideo", new MediaStream([cameraTrack]), true);
            setTileVideoState("local", true);
            setLocalStateText("Камера включена");
        } else {
            await replaceTrack("video", null);
            setTileVideoState("local", false);
            setLocalStateText(
                state.micEnabled ? "Микрофон включён" : "Микрофон выключен",
            );
        }
        updateControlVisuals();
        broadcastState();
    }

    function toggleMic() {
        state.micEnabled = !state.micEnabled;
        state.rawMicStream?.getAudioTracks().forEach((track) => {
            track.enabled = state.micEnabled;
        });
        if (state.micGain)
            state.micGain.gain.value = state.micEnabled ? state.micVolume : 0;
        const outgoing = state.localStream?.getAudioTracks()[0];
        if (outgoing && !state.screenStream?.getAudioTracks().length)
            outgoing.enabled = state.micEnabled;
        setLocalStateText(
            state.micEnabled ? "Микрофон включён" : "Микрофон выключен",
        );
        updateControlVisuals();
        broadcastState();
    }

    function updateControlVisuals() {
        const mic = $("callMicToggle");
        const camera = $("callCameraToggle");
        const screen = $("callScreenToggle");
        if (mic) {
            mic.classList.toggle("active", state.micEnabled);
            mic.classList.toggle("disabled", !state.micEnabled);
            mic.querySelector(".call-control__icon").innerHTML =
                state.micEnabled ? icons.mic : icons.micOff;
            mic.querySelector(".call-control__label").textContent =
                state.micEnabled ? "Микрофон" : "Без звука";
        }
        if (camera) {
            camera.classList.toggle("active", state.cameraEnabled);
            camera.querySelector(".call-control__icon").innerHTML =
                state.cameraEnabled ? icons.camera : icons.cameraOff;
        }
        if (screen) screen.classList.toggle("active", state.screenEnabled);
    }

    function setLocalStateText(text) {
        if ($("localCallState")) $("localCallState").textContent = text;
    }

    async function fetchTurnConfiguration() {
        let lastError = null;
        for (const endpoint of TURN_ENDPOINTS) {
            try {
                const controller = new AbortController();
                const timeoutId = setTimeout(() => controller.abort(), 5000);
                const response = await fetch(endpoint, {
                    signal: controller.signal,
                    cache: "no-store",
                    credentials: "same-origin",
                    headers: { Accept: "application/json" },
                });
                clearTimeout(timeoutId);
                if (!response.ok)
                    throw new Error(`${endpoint}: HTTP ${response.status}`);
                const data = await response.json();
                const urls = data.urls || data.url;
                if (!urls || !data.username || !data.credential) {
                    throw new Error(`${endpoint}: неполный ответ TURN`);
                }
                state.turnAvailable = true;
                updateDiagnostic("callTurnState", "доступен", "success");
                return [
                    {
                        urls,
                        username: data.username,
                        credential: data.credential,
                    },
                ];
            } catch (error) {
                lastError = error;
            }
        }
        state.turnAvailable = false;
        updateDiagnostic("callTurnState", "недоступен", "danger");
        console.warn(
            "[CALL] TURN credentials unavailable. Calls may fail behind NAT/firewall.",
            lastError,
        );
        return [];
    }

    async function createPeerConnection() {
        if (state.pc) return state.pc;
        const turnServers = await fetchTurnConfiguration();
        const iceServers = [...turnServers, ...DEFAULT_STUN_SERVERS];
        const pc = new RTCPeerConnection({
            iceServers,
            iceCandidatePoolSize: 10,
            iceTransportPolicy: "all",
            bundlePolicy: "max-bundle",
            rtcpMuxPolicy: "require",
        });
        state.pc = pc;
        state.remoteStream = new MediaStream();
        state.remoteAudioStream = new MediaStream();
        state.remoteVideoStream = new MediaStream();
        state.remoteMeterStarted = false;

        const remoteVideo = $("remoteCallVideo");
        if (remoteVideo) {
            remoteVideo.srcObject = state.remoteVideoStream;
            remoteVideo.muted = true;
        }
        const remoteAudio = $("remoteCallAudio");
        if (remoteAudio) {
            remoteAudio.srcObject = state.remoteAudioStream;
            remoteAudio.volume = state.speakerVolume;
        }
        await setSpeakerSink();

        const audioTransceiver = pc.addTransceiver("audio", {
            direction: "sendrecv",
        });
        const videoTransceiver = pc.addTransceiver("video", {
            direction: "sendrecv",
        });
        state.audioSender = audioTransceiver.sender;
        state.videoSender = videoTransceiver.sender;
        const audioTrack = state.localStream?.getAudioTracks()[0];
        if (audioTrack) await state.audioSender.replaceTrack(audioTrack);

        pc.ontrack = (event) => {
            addRemoteTrack(event.track);
            void playRemoteMedia();
            updateRemotePreview();
            if (
                !state.remoteMeterStarted &&
                state.remoteAudioStream.getAudioTracks().length
            ) {
                state.remoteMeterStarted = true;
                startVolumeMeter(state.remoteAudioStream, "remoteCallTile");
            }
        };

        pc.onicecandidate = (event) => {
            if (!event.candidate) return;
            if (event.candidate.type === "relay") {
                state.turnRelayDetected = true;
                updateDiagnostic("callTurnState", "relay найден", "success");
            }
            send("call_signal", {
                signal_type: "candidate",
                candidate: event.candidate.toJSON
                    ? event.candidate.toJSON()
                    : event.candidate,
            });
        };

        pc.onicecandidateerror = (event) => {
            console.warn("[CALL] ICE candidate error", {
                url: event.url,
                errorCode: event.errorCode,
                errorText: event.errorText,
            });
            updateDiagnostic(
                "callTransportText",
                `ICE ${event.errorCode || "error"}`,
                "danger",
            );
        };

        pc.oniceconnectionstatechange = () => {
            updateDiagnostic(
                "callIceState",
                pc.iceConnectionState,
                stateClass(pc.iceConnectionState),
            );
            if (["connected", "completed"].includes(pc.iceConnectionState)) {
                markConnected();
            } else if (pc.iceConnectionState === "failed") {
                void recoverIce("ice-failed");
            } else if (pc.iceConnectionState === "disconnected") {
                updatePhase("reconnecting", "Соединение временно потеряно");
            }
        };

        pc.onconnectionstatechange = () => {
            updateDiagnostic(
                "callWebRtcState",
                pc.connectionState,
                stateClass(pc.connectionState),
            );
            if (pc.connectionState === "connected") {
                markConnected();
            } else if (pc.connectionState === "failed") {
                void recoverIce("connection-failed");
            } else if (pc.connectionState === "closed") {
                updatePhase("failed", "Медиасоединение закрыто");
            }
        };

        pc.onsignalingstatechange = () => {
            console.debug("[CALL] signalingState", pc.signalingState);
        };

        await flushPendingSignals();
        return pc;
    }

    async function createOffer({ iceRestart = false } = {}) {
        const pc = await createPeerConnection();
        if (pc.signalingState !== "stable" && !iceRestart) return;
        const offer = await pc.createOffer({ iceRestart });
        await pc.setLocalDescription(offer);
        send("call_signal", {
            signal_type: "offer",
            sdp: pc.localDescription,
            ice_restart: iceRestart,
        });
    }

    async function handleOffer(sdp, from) {
        if (from) state.peerId = from;
        if (!state.inCall) {
            state.peerName = callPeer()?.nickname || state.peerName;
            state.isInitiator = false;
            await prepareCall({ phase: "connecting" });
        } else if (!state.pc) {
            state.pendingSignals.push({ signal_type: "offer", sdp, from });
            return;
        }
        const pc = state.pc;
        if (pc.signalingState !== "stable") {
            await pc.setLocalDescription({ type: "rollback" }).catch(() => {});
        }
        await pc.setRemoteDescription(new RTCSessionDescription(sdp));
        await flushCandidates();
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        send("call_signal", {
            signal_type: "answer",
            sdp: pc.localDescription,
        });
    }

    async function handleAnswer(sdp) {
        if (!state.pc) {
            state.pendingSignals.push({ signal_type: "answer", sdp });
            return;
        }
        if (state.pc.signalingState !== "have-local-offer") return;
        await state.pc.setRemoteDescription(new RTCSessionDescription(sdp));
        await flushCandidates();
    }

    async function handleCandidate(candidate) {
        if (!candidate) return;
        if (!state.pc?.remoteDescription) {
            state.pendingCandidates.push(candidate);
            return;
        }
        await state.pc.addIceCandidate(new RTCIceCandidate(candidate));
    }

    async function flushCandidates() {
        while (state.pendingCandidates.length && state.pc?.remoteDescription) {
            const candidate = state.pendingCandidates.shift();
            await state.pc.addIceCandidate(new RTCIceCandidate(candidate));
        }
    }

    async function flushPendingSignals() {
        if (!state.pc || !state.pendingSignals.length) return;
        const signals = state.pendingSignals.splice(0);
        for (const signal of signals) {
            if (signal.signal_type === "offer")
                await handleOffer(signal.sdp, signal.from);
            if (signal.signal_type === "answer") await handleAnswer(signal.sdp);
            if (signal.signal_type === "candidate")
                await handleCandidate(signal.candidate);
        }
    }

    async function recoverIce(reason) {
        if (!state.inCall || !state.pc) return;
        if (state.iceRestartCount >= MAX_ICE_RESTARTS) {
            updatePhase(
                "failed",
                state.turnAvailable
                    ? "TURN доступен, но медиамаршрут не установился"
                    : "TURN недоступен — проверьте сервер и открытые порты",
            );
            toast(
                "Не удалось восстановить звонок",
                state.turnAvailable
                    ? "Проверьте сеть и повторите звонок."
                    : "Сервер TURN не выдал relay-кандидат. На разных сетях звонок без TURN часто невозможен.",
                "danger",
            );
            return;
        }
        state.iceRestartCount += 1;
        updatePhase(
            "reconnecting",
            `Перезапуск ICE: попытка ${state.iceRestartCount}`,
        );
        if (state.isInitiator) {
            state.pc.restartIce?.();
            await createOffer({ iceRestart: true });
        } else {
            send("call_signal", { signal_type: "restart_request", reason });
        }
    }

    function markConnected() {
        if (!state.inCall) return;
        clearTimeout(state.connectTimer);
        state.connectTimer = null;
        state.iceRestartCount = 0;
        updatePhase("connected");
        updateDiagnostic(
            "callTransportText",
            state.turnAvailable ? "P2P / TURN fallback" : "P2P / STUN only",
            state.turnAvailable ? "success" : "warning",
        );
        if (!state.callStartedAt) {
            state.callStartedAt = Date.now();
            startDurationTimer();
            addChatSystemMessage(`Звонок с ${state.peerName} начался.`);
        }
        void playRemoteMedia();
    }

    function armConnectTimeout() {
        clearTimeout(state.connectTimer);
        state.connectTimer = setTimeout(() => {
            if (
                !state.inCall ||
                ["connected", "closed"].includes(state.pc?.connectionState)
            )
                return;
            updatePhase(
                "failed",
                state.turnAvailable
                    ? "Соединение устанавливается слишком долго"
                    : "TURN недоступен: прямой P2P-маршрут не найден",
            );
        }, CALL_CONNECT_TIMEOUT_MS);
    }

    function startDurationTimer() {
        clearInterval(state.durationTimer);
        const update = () => {
            const elapsed = Math.max(0, Date.now() - state.callStartedAt);
            const totalSeconds = Math.floor(elapsed / 1000);
            const hours = Math.floor(totalSeconds / 3600);
            const minutes = Math.floor((totalSeconds % 3600) / 60);
            const seconds = totalSeconds % 60;
            const value = hours
                ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
                : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
            if ($("callDuration")) $("callDuration").textContent = value;
        };
        update();
        state.durationTimer = setInterval(update, 1000);
    }

    async function replaceTrack(kind, track) {
        if (!state.pc) return;
        const sender = kind === "audio" ? state.audioSender : state.videoSender;
        if (sender) await sender.replaceTrack(track || null);
    }

    function addRemoteTrack(track) {
        const target =
            track.kind === "audio"
                ? state.remoteAudioStream
                : state.remoteVideoStream;
        [state.remoteStream, target].forEach((stream) => {
            if (
                stream &&
                !stream.getTracks().some((item) => item.id === track.id)
            )
                stream.addTrack(track);
        });
        track.onunmute = () => {
            void playRemoteMedia();
            updateRemotePreview();
        };
        track.onmute = updateRemotePreview;
        track.onended = () => {
            state.remoteStream?.removeTrack(track);
            state.remoteAudioStream?.removeTrack(track);
            state.remoteVideoStream?.removeTrack(track);
            updateRemotePreview();
        };
    }

    async function playRemoteMedia() {
        const remoteAudio = $("remoteCallAudio");
        const remoteVideo = $("remoteCallVideo");
        if (remoteAudio) {
            remoteAudio.srcObject = state.remoteAudioStream;
            remoteAudio.volume = state.speakerVolume;
            try {
                await remoteAudio.play();
                state.audioUnlocked = true;
                if ($("callAudioUnlock")) $("callAudioUnlock").hidden = true;
            } catch (error) {
                console.warn("[CALL] Remote audio autoplay blocked", error);
                state.audioUnlocked = false;
                if ($("callAudioUnlock")) $("callAudioUnlock").hidden = false;
            }
        }
        if (remoteVideo) {
            remoteVideo.srcObject = state.remoteVideoStream;
            remoteVideo.muted = true;
            await remoteVideo.play().catch(() => {});
        }
    }

    async function unlockRemoteAudio() {
        const remoteAudio = $("remoteCallAudio");
        if (!remoteAudio) return;
        try {
            await remoteAudio.play();
            state.audioUnlocked = true;
            $("callAudioUnlock").hidden = true;
            toast(
                "Звук включён",
                "Аудио собеседника теперь воспроизводится.",
                "success",
            );
        } catch (error) {
            toast(
                "Звук заблокирован",
                "Разрешите воспроизведение звука в настройках браузера.",
                "danger",
            );
        }
    }

    function updateRemotePreview() {
        const hasVideo = state.remoteVideoStream
            ?.getVideoTracks()
            .some((track) => track.readyState === "live" && !track.muted);
        const hasAudio = state.remoteAudioStream
            ?.getAudioTracks()
            .some((track) => track.readyState === "live");
        setTileVideoState("remote", Boolean(hasVideo));
        if ($("remoteCallState")) {
            $("remoteCallState").textContent = hasVideo
                ? "Видео подключено"
                : hasAudio
                  ? "Аудио подключено"
                  : "Ожидаем медиа";
        }
    }

    function setVideo(id, stream, muted) {
        const video = $(id);
        if (!video) return;
        video.srcObject = stream;
        video.muted = muted;
        video.play().catch(() => {});
    }

    function setTileVideoState(kind, hasVideo) {
        const video = $(`${kind}CallVideo`);
        const empty = $(`${kind}CallEmpty`);
        video?.classList.toggle("is-hidden", !hasVideo);
        if (empty) empty.hidden = hasVideo;
        $(`${kind}CallTile`)?.classList.toggle("has-video", hasVideo);
    }

    async function loadDevices() {
        if (!navigator.mediaDevices?.enumerateDevices) return;
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            fillSelect(
                "callMicSelect",
                devices.filter((device) => device.kind === "audioinput"),
                "Микрофон",
                state.selectedMicId,
                (value) => (state.selectedMicId = value),
            );
            fillSelect(
                "callSpeakerSelect",
                devices.filter((device) => device.kind === "audiooutput"),
                "Динамик",
                state.selectedSpeakerId,
                (value) => (state.selectedSpeakerId = value),
            );
            fillSelect(
                "callCameraSelect",
                devices.filter((device) => device.kind === "videoinput"),
                "Камера",
                state.selectedCameraId,
                (value) => (state.selectedCameraId = value),
            );
            await setSpeakerSink();
        } catch (error) {
            console.warn("[CALL] enumerateDevices failed", error);
        }
    }

    function fillSelect(id, devices, fallback, selectedId, onSelected) {
        const select = $(id);
        if (!select) return;
        const previous = selectedId || select.value || "";
        select.innerHTML = [
            `<option value="">${safe(fallback)} по умолчанию</option>`,
            ...devices.map(
                (device, index) =>
                    `<option value="${safeAttr(device.deviceId)}">${safe(device.label || `${fallback} ${index + 1}`)}</option>`,
            ),
        ].join("");
        if (
            previous &&
            devices.some((device) => device.deviceId === previous)
        ) {
            select.value = previous;
        }
        onSelected?.(select.value);
    }

    async function setSpeakerSink() {
        const remoteAudio = $("remoteCallAudio");
        const select = $("callSpeakerSelect");
        const deviceId = select?.value || state.selectedSpeakerId || "";
        if (!remoteAudio?.setSinkId) {
            if (select) select.disabled = true;
            setDeviceHint(
                "callSpeakerPermissionStatus",
                "Выбор динамика не поддерживается браузером",
                false,
            );
            return;
        }
        try {
            await remoteAudio.setSinkId(deviceId);
            setDeviceHint(
                "callSpeakerPermissionStatus",
                deviceId
                    ? "Выбрано отдельное устройство вывода"
                    : "Системное устройство по умолчанию",
                false,
            );
        } catch (error) {
            setDeviceHint(
                "callSpeakerPermissionStatus",
                "Не удалось выбрать динамик",
                true,
            );
        }
    }

    async function pickSpeakerOutput() {
        if (!navigator.mediaDevices?.selectAudioOutput) {
            toast(
                "Выбор динамика недоступен",
                "Используется системное устройство вывода.",
                "info",
            );
            return;
        }
        try {
            const device = await navigator.mediaDevices.selectAudioOutput();
            state.selectedSpeakerId = device.deviceId;
            await loadDevices();
            if ($("callSpeakerSelect"))
                $("callSpeakerSelect").value = device.deviceId;
            await setSpeakerSink();
        } catch (error) {
            console.warn("[CALL] selectAudioOutput failed", error);
        }
    }

    function testSpeaker() {
        const AudioContextClass =
            window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return;
        const context = new AudioContextClass();
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        const destination = context.createMediaStreamDestination();
        const audio = new Audio();
        oscillator.frequency.value = 660;
        gain.gain.value = Math.max(0.02, state.speakerVolume * 0.15);
        oscillator.connect(gain).connect(destination);
        audio.srcObject = destination.stream;
        audio.volume = state.speakerVolume;
        const deviceId = $("callSpeakerSelect")?.value || "";
        if (audio.setSinkId) audio.setSinkId(deviceId).catch(() => {});
        audio.play().catch(() => {});
        oscillator.start();
        setTimeout(() => {
            oscillator.stop();
            audio.pause();
            context.close();
        }, 500);
    }

    function startLocalMeter() {
        if (state.localMeterStarted) return;
        const sourceStream = state.rawMicStream || state.localStream;
        if (!sourceStream?.getAudioTracks().length) return;
        state.localMeterStarted = true;
        startVolumeMeter(sourceStream, "localCallTile", (speaking) => {
            send("call_state", {
                speaking,
                micEnabled: state.micEnabled,
                cameraEnabled: state.cameraEnabled,
                screenEnabled: state.screenEnabled,
            });
        });
    }

    function startVolumeMeter(stream, tileId, onSpeakingChange) {
        if (!stream?.getAudioTracks().length) return;
        const AudioContextClass =
            window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) return;
        const context = new AudioContextClass();
        const analyser = context.createAnalyser();
        analyser.fftSize = 512;
        context.createMediaStreamSource(stream).connect(analyser);
        const data = new Uint8Array(analyser.frequencyBinCount);
        let rafId = 0;
        let lastSpeaking = false;
        let lastSentAt = 0;
        const tick = () => {
            if (!state.inCall) {
                cancelAnimationFrame(rafId);
                context.close().catch(() => {});
                return;
            }
            analyser.getByteFrequencyData(data);
            const average =
                data.reduce((sum, value) => sum + value, 0) / data.length;
            const speaking = average > 16;
            $(tileId)?.classList.toggle("speaking", speaking);
            if (tileId === "localCallTile" && $("callMicMeter")) {
                $("callMicMeter").style.width =
                    `${Math.min(100, average * 2.8)}%`;
            }
            if (speaking !== lastSpeaking && Date.now() - lastSentAt > 250) {
                lastSpeaking = speaking;
                lastSentAt = Date.now();
                onSpeakingChange?.(speaking);
            }
            rafId = requestAnimationFrame(tick);
        };
        tick();
        state.meters.push({ context, rafId });
    }

    function broadcastState(extra = {}) {
        send("call_state", {
            micEnabled: state.micEnabled,
            cameraEnabled: state.cameraEnabled,
            screenEnabled: state.screenEnabled,
            ...extra,
        });
    }

    function subscribeDeviceChanges() {
        if (!navigator.mediaDevices || state.deviceChangeHandler) return;
        state.deviceChangeHandler = () => void loadDevices();
        navigator.mediaDevices.addEventListener?.(
            "devicechange",
            state.deviceChangeHandler,
        );
    }

    function unsubscribeDeviceChanges() {
        if (!navigator.mediaDevices || !state.deviceChangeHandler) return;
        navigator.mediaDevices.removeEventListener?.(
            "devicechange",
            state.deviceChangeHandler,
        );
        state.deviceChangeHandler = null;
    }

    function rememberSelectedDevice(kind, track) {
        const deviceId = track?.getSettings?.().deviceId;
        if (!deviceId) return;
        if (kind === "audioinput") state.selectedMicId = deviceId;
        if (kind === "videoinput") state.selectedCameraId = deviceId;
    }

    function setDeviceHint(id, text, isError = false) {
        const node = $(id);
        if (!node) return;
        node.textContent = text;
        node.classList.toggle("is-error", isError);
    }

    function updateDiagnostic(id, text, className = "") {
        const node = $(id);
        if (!node) return;
        node.textContent = text;
        node.dataset.state = className;
    }

    function stateClass(value) {
        if (["connected", "completed", "stable"].includes(value))
            return "success";
        if (["failed", "closed"].includes(value)) return "danger";
        if (["disconnected"].includes(value)) return "warning";
        return "pending";
    }

    function mediaErrorText(error, deviceName = "устройству") {
        if (error?.code === "INSECURE_CONTEXT") return error.message;
        if (error?.name === "NotAllowedError") {
            return `Доступ к ${deviceName} запрещён. Разрешите его в настройках сайта.`;
        }
        if (error?.name === "NotFoundError")
            return `Устройство для доступа к ${deviceName} не найдено.`;
        if (error?.name === "NotReadableError")
            return `Устройство для доступа к ${deviceName} занято другой программой.`;
        if (error?.name === "OverconstrainedError")
            return `Выбранное устройство для доступа к ${deviceName} недоступно.`;
        if (error?.name === "AbortError")
            return `Операция доступа к ${deviceName} отменена.`;
        return error?.message || `Не удалось получить доступ к ${deviceName}.`;
    }

    function stopTracks(stream) {
        stream?.getTracks?.().forEach((track) => track.stop());
    }

    function endCall(notifyPeer, reason = "Звонок завершён") {
        if (notifyPeer && state.peerId) send("call_ended", { reason });
        cleanupActiveCall(false);
        addChatSystemMessage(reason);
    }

    function cleanupActiveCall(showMessage = true) {
        clearTimeout(state.answerTimer);
        clearTimeout(state.connectTimer);
        clearInterval(state.durationTimer);
        state.answerTimer = null;
        state.connectTimer = null;
        state.durationTimer = null;
        unsubscribeDeviceChanges();

        try {
            state.pc
                ?.getSenders?.()
                .forEach((sender) => sender.replaceTrack(null).catch(() => {}));
            state.pc?.close?.();
        } catch (_) {}

        stopTracks(state.localStream);
        stopTracks(state.rawMicStream);
        stopTracks(state.cameraStream);
        stopTracks(state.screenStream);
        stopTracks(state.remoteStream);
        state.meters.forEach(({ context, rafId }) => {
            cancelAnimationFrame(rafId);
            context?.close?.().catch(() => {});
        });
        state.audioContext?.close?.().catch(() => {});

        document
            .querySelectorAll(".call-overlay,.incoming-call-overlay")
            .forEach((node) => node.remove());
        setBodyCallMode(false);

        const preserved = {
            selectedMicId: state.selectedMicId,
            selectedCameraId: state.selectedCameraId,
            selectedSpeakerId: state.selectedSpeakerId,
            noiseSuppression: state.noiseSuppression,
            micVolume: state.micVolume,
            speakerVolume: state.speakerVolume,
        };
        Object.assign(state, {
            pc: null,
            peerId: null,
            peerName: "Собеседник",
            inCall: false,
            isInitiator: false,
            phase: "idle",
            localStream: null,
            rawMicStream: null,
            cameraStream: null,
            screenStream: null,
            remoteStream: null,
            remoteAudioStream: null,
            remoteVideoStream: null,
            audioContext: null,
            audioDestination: null,
            micGain: null,
            screenGain: null,
            audioSender: null,
            videoSender: null,
            micEnabled: true,
            cameraEnabled: false,
            screenEnabled: false,
            pendingCandidates: [],
            pendingSignals: [],
            preparingPromise: null,
            turnAvailable: false,
            turnRelayDetected: false,
            iceRestartCount: 0,
            callStartedAt: 0,
            meters: [],
            remoteMeterStarted: false,
            localMeterStarted: false,
            audioUnlocked: false,
            ...preserved,
        });
        if (showMessage)
            addChatSystemMessage("Звонок завершён. Вы вернулись в чат.");
    }

    window.startOutgoingCall = async function startOutgoingCall() {
        if (state.inCall) {
            toast(
                "Звонок уже активен",
                "Завершите текущий вызов перед новым.",
                "info",
            );
            return;
        }
        const peer = callPeer();
        if (!peer?.id) {
            toast(
                "Звонок недоступен",
                "Сначала дождитесь подключения собеседника к чату.",
                "danger",
            );
            return;
        }
        state.peerId = peer.id;
        state.peerName = peer.nickname || "Собеседник";
        state.isInitiator = true;
        try {
            await prepareCall({ phase: "ringing" });
            send("call_request", {});
            updatePhase("ringing", `Звоним пользователю ${state.peerName}…`);
            state.answerTimer = setTimeout(() => {
                if (state.phase !== "ringing") return;
                send("call_ended", { reason: "Нет ответа" });
                cleanupActiveCall(false);
                addChatSystemMessage("Собеседник не ответил на звонок.");
            }, CALL_ANSWER_TIMEOUT_MS);
        } catch (error) {
            console.error("[CALL] Unable to start call", error);
            cleanupActiveCall(false);
            toast("Звонок недоступен", mediaErrorText(error), "danger");
        }
    };

    window.handleCallMessage = async function handleCallMessage(msg) {
        const data = msg?.data || {};
        try {
            if (msg.type === "call_request") {
                if (state.inCall) {
                    send("call_response", {
                        to: data.from,
                        accepted: false,
                        reason: "busy",
                    });
                    return;
                }
                createIncomingModal(
                    data.from_nickname || callPeer()?.nickname || "Собеседник",
                    data.from,
                );
                return;
            }

            if (msg.type === "call_response") {
                clearTimeout(state.answerTimer);
                state.answerTimer = null;
                if (!data.accepted) {
                    cleanupActiveCall(false);
                    addChatSystemMessage(
                        data.reason === "busy"
                            ? "Собеседник уже разговаривает."
                            : "Собеседник отклонил звонок.",
                    );
                    return;
                }
                state.isInitiator = true;
                updatePhase("connecting");
                await createOffer();
                return;
            }

            if (msg.type === "call_signal") {
                const signal = {
                    signal_type: data.signal_type,
                    sdp: data.sdp,
                    candidate: data.candidate,
                    from: data.from,
                };
                if (!state.pc && state.preparingPromise) {
                    state.pendingSignals.push(signal);
                    return;
                }
                if (data.signal_type === "offer")
                    await handleOffer(data.sdp, data.from);
                if (data.signal_type === "answer") await handleAnswer(data.sdp);
                if (data.signal_type === "candidate")
                    await handleCandidate(data.candidate);
                if (data.signal_type === "restart_request") {
                    state.isInitiator = true;
                    await createOffer({ iceRestart: true });
                }
                return;
            }

            if (msg.type === "call_ended") {
                cleanupActiveCall(false);
                addChatSystemMessage(
                    data.reason || "Собеседник завершил звонок.",
                );
                return;
            }

            if (msg.type === "call_state") {
                $("remoteCallTile")?.classList.toggle(
                    "speaking",
                    Boolean(data.speaking),
                );
                if ($("remoteCallState")) {
                    $("remoteCallState").textContent = data.screenEnabled
                        ? "Демонстрирует экран"
                        : data.cameraEnabled
                          ? "Камера включена"
                          : data.micEnabled === false
                            ? "Микрофон выключен"
                            : "В разговоре";
                }
            }
        } catch (error) {
            console.error("[CALL] handleCallMessage failed", error, msg);
            toast(
                "Ошибка звонка",
                error?.message || "Не удалось обработать сигнал звонка.",
                "danger",
            );
        }
    };

    window.cleanupActiveCall = cleanupActiveCall;

    window.addEventListener("beforeunload", () => {
        if (state.inCall && state.peerId)
            send("call_ended", { reason: "Собеседник покинул страницу" });
        cleanupActiveCall(false);
    });
})();
