// Online voice/video calls for the encrypted chat.
(() => {
    const defaultIceServers = [
        { urls: "stun:stun.l.google.com:19302" },
        { urls: "stun:stun1.l.google.com:19302" },
        { urls: "stun:stun.cloudflare.com:3478" },
    ];

    const icons = {
        phone: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.8 19.8 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.11 4.18 2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.12.9.33 1.77.63 2.61a2 2 0 0 1-.45 2.11L8.09 9.64a16 16 0 0 0 6.27 6.27l1.2-1.2a2 2 0 0 1 2.11-.45c.84.3 1.71.51 2.61.63A2 2 0 0 1 22 16.92z"/></svg>',
        phoneOff:
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.68 13.31a16 16 0 0 0 3.01 3.01l1.2-1.2a2 2 0 0 1 2.11-.45c.84.3 1.71.51 2.61.63A2 2 0 0 1 21.33 17v3a2 2 0 0 1-2.18 2A19.8 19.8 0 0 1 3.02 5.85 2 2 0 0 1 5 3.67h3a2 2 0 0 1 1.7 1.72c.12.9.33 1.77.63 2.61a2 2 0 0 1-.45 2.11l-1.2 1.2z"/><path d="M22 2 2 22"/></svg>',
        mic: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><path d="M12 19v3"/></svg>',
        camera: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M23 7 16 12l7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2"/></svg>',
        screen: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/></svg>',
        settings:
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 15.5A3.5 3.5 0 1 0 12 8a3.5 3.5 0 0 0 0 7.5z"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06A1.65 1.65 0 0 0 15 19.4a1.65 1.65 0 0 0-1 .6 1.65 1.65 0 0 0-.33 1.82 2 2 0 1 1-3.34 0A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.6 15a1.65 1.65 0 0 0-.6-1 1.65 1.65 0 0 0-1.82-.33 2 2 0 1 1 0-3.34A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-.6 1.65 1.65 0 0 0 .33-1.82 2 2 0 1 1 3.34 0A1.65 1.65 0 0 0 15 4.6a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9c.23.38.43.77.6 1.18a1.65 1.65 0 0 0 1.82.33 2 2 0 1 1 0 3.34A1.65 1.65 0 0 0 19.4 15z"/></svg>',
    };

    const state = {
        pc: null,
        localStream: null,
        remoteStream: null,
        remoteAudioStream: null,
        remoteVideoStream: null,
        screenStream: null,
        micContext: null,
        micGain: null,
        micDestination: null,
        rawMicStream: null,
        peerId: null,
        peerName: "Собеседник",
        inCall: false,
        micEnabled: true,
        cameraEnabled: false,
        screenEnabled: false,
        noiseSuppression: true,
        micVolume: 1,
        speakerVolume: 1,
        pendingCandidates: [],
        meters: [],
        selectedMicId: "",
        selectedSpeakerId: "",
        selectedCameraId: "",
        audioSender: null,
        videoSender: null,
        deviceChangeHandler: null,
        remoteMeterStarted: false,
    };

    const $ = (id) => document.getElementById(id);
    const safe = (value) =>
        window.escapeHtml
            ? window.escapeHtml(value || "")
            : String(value || "");
    const safeAttr = (value) =>
        window.escapeAttr
            ? window.escapeAttr(String(value || ""))
            : safe(value).replace(/"/g, "&quot;");
    const initial = (name) =>
        safe(name).trim().slice(0, 1).toUpperCase() || "?";
    const callPeer = () =>
        window.currentChatPeer || {
            id: window.peerId,
            nickname: window.peerNickname,
        };

    function send(type, data = {}) {
        if (!window.ws || window.ws.readyState !== WebSocket.OPEN) return false;
        window.ws.send(JSON.stringify({ type, data }));
        return true;
    }

    function toast(title, text = "", variant = "success") {
        const node = document.createElement("div");
        node.className = `call-toast ${variant}`;
        node.innerHTML = `<div class="call-toast-title">${safe(title)}</div><div class="call-toast-text">${safe(text)}</div>`;
        document.body.appendChild(node);
        setTimeout(() => node.remove(), 4200);
    }

    function addChatSystemMessage(text) {
        if (typeof window.addSystemMessage === "function")
            window.addSystemMessage(text);
        else toast("Звонок", text);
    }

    function createIncomingModal(fromName, fromId) {
        document
            .querySelectorAll(".incoming-call-overlay")
            .forEach((n) => n.remove());
        const modal = document.createElement("div");
        modal.className = "incoming-call-overlay";
        modal.innerHTML = `
            <div class="incoming-call-card">
                <div class="incoming-call-avatar">${initial(fromName)}</div>
                <div class="incoming-call-kicker">Входящий звонок</div>
                <div class="incoming-call-name">${safe(fromName)}</div>
                <div class="incoming-call-text">Хочет созвониться с вами. Ответьте или сбросьте звонок как на телефоне.</div>
                <div class="incoming-call-actions">
                    <button class="call-round-btn reject" id="callRejectIncoming" title="Сбросить">${icons.phoneOff}</button>
                    <button class="call-round-btn accept" id="callAcceptIncoming" title="Ответить">${icons.phone}</button>
                </div>
            </div>`;
        document.body.appendChild(modal);
        $("callRejectIncoming").onclick = () => {
            modal.remove();
            send("call_response", { to: fromId, accepted: false });
            addChatSystemMessage("Вы сбросили входящий звонок.");
        };
        $("callAcceptIncoming").onclick = async () => {
            modal.remove();
            state.peerId = fromId;
            state.peerName = fromName;
            await requestInitialMediaPermissions();
            send("call_response", { to: fromId, accepted: true });
            await openCall(false);
        };
    }

    function renderCallOverlay() {
        document.querySelectorAll(".call-overlay").forEach((n) => n.remove());
        const node = document.createElement("div");
        node.className = "call-overlay";
        node.id = "callOverlay";
        node.innerHTML = `
            <header class="call-topbar">
                <div class="call-brand">
                    <div class="call-brand-mark">${icons.phone}</div>
                    <div><div class="call-title">Голосовой канал</div><div class="call-subtitle" id="callSubtitle">Защищённый P2P-звонок с ${safe(state.peerName)}</div></div>
                </div>
                <div class="call-status-pill" id="callStatusPill">Соединяемся...</div>
            </header>
            <main class="call-stage">
                <section class="call-grid">
                    ${tileHtml("local", "Вы", true)}
                    ${tileHtml("remote", state.peerName, false)}
                </section>
                <aside class="call-sidepanel" id="callSettingsPanel">
                    <h3>Настройки звонка</h3>
                    <section class="call-settings-section">
                        <h3>Микрофон</h3>
                        <label class="call-label" for="callMicSelect">Устройство ввода</label>
                        <select class="call-select" id="callMicSelect"></select>
                        <div class="call-device-hint" id="callMicPermissionStatus">Разрешение микрофона ещё не запрошено</div>
                        <label class="call-label" for="callMicVolume">Громкость микрофона: <span id="callMicVolumeText">100%</span></label>
                        <input class="call-range" id="callMicVolume" type="range" min="0" max="100" value="100">
                        <div class="call-test-row"><button class="call-mini-btn" id="callRefreshDevices">Обновить устройства</button><button class="call-mini-btn" id="callTestMic">Проверить</button><div class="call-meter"><div class="call-meter-fill" id="callMicMeter"></div></div></div>
                    </section>
                    <section class="call-settings-section">
                        <h3>Динамики</h3>
                        <label class="call-label" for="callSpeakerSelect">Устройство вывода</label>
                        <select class="call-select" id="callSpeakerSelect"></select>
                        <div class="call-device-hint" id="callSpeakerPermissionStatus">Будет использован системный динамик по умолчанию</div>
                        <label class="call-label" for="callSpeakerVolume">Громкость динамиков: <span id="callSpeakerVolumeText">100%</span></label>
                        <input class="call-range" id="callSpeakerVolume" type="range" min="0" max="100" value="100">
                        <div class="call-test-row"><button class="call-mini-btn" id="callPickSpeaker">Выбрать динамик</button><button class="call-mini-btn" id="callTestSpeaker">Проверить динамик</button><div class="call-meter"><div class="call-meter-fill" id="callSpeakerMeter"></div></div></div>
                    </section>
                    <section class="call-settings-section">
                        <label class="call-toggle-line">Шумоподавление <input id="callNoiseSuppression" type="checkbox" checked></label>
                    </section>
                    <section class="call-settings-section">
                        <h3>Камера</h3>
                        <label class="call-label" for="callCameraSelect">Устройство камеры</label>
                        <select class="call-select" id="callCameraSelect"></select>
                        <div class="call-device-hint" id="callCameraPermissionStatus">Разрешение камеры ещё не запрошено</div>
                        <div class="call-test-row"><button class="call-mini-btn" id="callTestCamera">Проверить камеру</button><div class="call-meter"><div class="call-meter-fill" id="callCameraMeter"></div></div></div>
                    </section>
                </aside>
            </main>
            <footer class="call-controls">
                <button class="call-control active" id="callMicToggle" title="Микрофон">${icons.mic}</button>
                <button class="call-control" id="callCameraToggle" title="Камера">${icons.camera}</button>
                <button class="call-control" id="callScreenToggle" title="Демонстрация экрана">${icons.screen}</button>
                <button class="call-control active" id="callSettingsToggle" title="Настройки">${icons.settings}</button>
                <button class="call-control end" id="callEndButton" title="Сбросить звонок">${icons.phoneOff}</button>
            </footer>`;
        document.body.appendChild(node);
    }

    function tileHtml(kind, name, muted) {
        return `<article class="call-tile" id="${kind}CallTile">
            <video id="${kind}CallVideo" ${muted ? "muted" : ""} autoplay playsinline class="is-hidden"></video>
            ${kind === "remote" ? '<audio id="remoteCallAudio" autoplay playsinline></audio>' : ""}
            <div class="call-empty-camera" id="${kind}CallEmpty"><div><div class="call-avatar">${initial(name)}</div><p>Камера выключена</p></div></div>
            <div class="call-tile-footer"><div><div class="call-person-name">${safe(name)}</div><div class="call-person-state" id="${kind}CallState">${kind === "local" ? "Готов к разговору" : "Ожидаем видео"}</div></div><span>●</span></div>
        </article>`;
    }

    async function openCall(isInitiator) {
        if (state.inCall) return;
        state.inCall = true;
        renderCallOverlay();
        bindControls();
        await loadDevices();
        await requestCallMediaPermissions();
        await loadDevices();
        await createPeerConnection();
        subscribeDeviceChanges();
        if (isInitiator) await createOffer();
        toast("Звонок начался", `Вы в звонке с ${state.peerName}`);
    }

    async function setupLocalMedia() {
        await requestCallMediaPermissions({ requestCamera: false });
    }

    async function requestInitialMediaPermissions() {
        try {
            await requestMicrophonePermission({ attachToCall: false });
            await requestCameraPermission({ keepTrack: false });
            await loadDevices();
        } catch (error) {
            console.warn(
                "[CALL] Initial media permission request failed",
                error,
            );
        }
    }

    async function requestCallMediaPermissions({ requestCamera = true } = {}) {
        if (!navigator.mediaDevices?.getUserMedia) {
            const reason = window.isSecureContext
                ? "Браузер не поддерживает доступ к микрофону/камере."
                : "Откройте сайт по HTTPS или localhost: браузер блокирует микрофон и камеру на небезопасных страницах.";
            setDeviceHint("callMicPermissionStatus", reason, true);
            setDeviceHint("callCameraPermissionStatus", reason, true);
            toast("Медиа недоступны", reason, "danger");
            state.localStream = state.localStream || new MediaStream();
            return;
        }

        await requestMicrophonePermission({ attachToCall: true });
        if (requestCamera) await requestCameraPermission({ keepTrack: false });
        await loadDevices();

        startVolumeMeter(state.localStream, "localCallTile", (speaking) => {
            send("call_state", {
                to: state.peerId,
                speaking,
                micEnabled: state.micEnabled,
                cameraEnabled: state.cameraEnabled,
                screenEnabled: state.screenEnabled,
            });
        });
    }

    async function requestMicrophonePermission({ attachToCall }) {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: audioConstraints(),
                video: false,
            });
            if (attachToCall) {
                await setLocalAudioStream(stream);
                if (!state.screenEnabled) updateLocalPreview();
            } else {
                stopTracks(stream);
            }
            const track = stream.getAudioTracks()[0];
            rememberSelectedDevice("audioinput", track);
            setDeviceHint(
                "callMicPermissionStatus",
                `Микрофон разрешён: ${track?.label || "системное устройство по умолчанию"}`,
            );
        } catch (error) {
            console.warn("[CALL] Microphone unavailable", error);
            const text = mediaErrorText(error, "микрофону");
            setDeviceHint("callMicPermissionStatus", text, true);
            toast("Микрофон недоступен", text, "danger");
            if (attachToCall) {
                const existingVideoTracks =
                    state.localStream?.getVideoTracks?.() || [];
                state.localStream = new MediaStream(existingVideoTracks);
                await replaceTrack("audio", null);
            }
        }
    }

    async function requestCameraPermission({ keepTrack }) {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                video: videoConstraints(),
                audio: false,
            });
            const track = stream.getVideoTracks()[0];
            rememberSelectedDevice("videoinput", track);
            setDeviceHint(
                "callCameraPermissionStatus",
                `Камера разрешена: ${track?.label || "системное устройство по умолчанию"}`,
            );
            if (!keepTrack) stopTracks(stream);
            return stream;
        } catch (error) {
            console.warn("[CALL] Camera unavailable", error);
            setDeviceHint(
                "callCameraPermissionStatus",
                mediaErrorText(error, "камере"),
                true,
            );
            return null;
        }
    }

    function audioConstraints() {
        const deviceId = $("callMicSelect")?.value || state.selectedMicId;
        return {
            deviceId: deviceId ? { exact: deviceId } : undefined,
            echoCancellation: true,
            noiseSuppression: state.noiseSuppression,
            autoGainControl: true,
        };
    }

    function videoConstraints() {
        const deviceId = $("callCameraSelect")?.value || state.selectedCameraId;
        return {
            deviceId: deviceId ? { exact: deviceId } : undefined,
            width: { ideal: 1280 },
            height: { ideal: 720 },
        };
    }

    async function setLocalAudioStream(inputStream) {
        const existingVideoTracks = state.localStream?.getVideoTracks?.() || [];
        state.localStream?.getAudioTracks?.().forEach((track) => track.stop());
        stopTracks(state.rawMicStream);
        state.rawMicStream = inputStream;
        state.localStream = new MediaStream([
            ...inputStream.getAudioTracks(),
            ...existingVideoTracks,
        ]);
        state.localStream
            .getAudioTracks()
            .forEach((track) => (track.enabled = state.micEnabled));
        await replaceTrack("audio", state.localStream.getAudioTracks()[0]);
    }

    function rememberSelectedDevice(kind, track) {
        const deviceId = track?.getSettings?.().deviceId;
        if (!deviceId) return;
        if (kind === "audioinput" && !state.selectedMicId)
            state.selectedMicId = deviceId;
        if (kind === "videoinput" && !state.selectedCameraId)
            state.selectedCameraId = deviceId;
    }

    function mediaErrorText(error, deviceName) {
        if (error?.name === "NotAllowedError")
            return `Доступ к ${deviceName} запрещён. Разрешите доступ в настройках браузера и нажмите «Обновить устройства».`;
        if (error?.name === "NotFoundError")
            return `Браузер не нашёл доступное устройство для ${deviceName}.`;
        if (error?.name === "NotReadableError")
            return `Устройство для ${deviceName} занято другой программой или недоступно системе.`;
        return `Не удалось получить доступ к ${deviceName}: ${error?.message || "неизвестная ошибка"}.`;
    }

    function setDeviceHint(id, text, isError = false) {
        const node = $(id);
        if (!node) return;
        node.textContent = text;
        node.classList.toggle("danger", isError);
    }

    async function getCallIceServers() {
        try {
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 3000);
            const res = await fetch("/turn-credentials", {
                signal: controller.signal,
            });
            clearTimeout(timeoutId);
            if (!res.ok) throw new Error(`TURN error ${res.status}`);
            const { username, credential, urls } = await res.json();
            if (!urls || !username || !credential) {
                throw new Error("TURN credentials response is incomplete");
            }
            return [{ urls, username, credential }, ...defaultIceServers];
        } catch (error) {
            console.warn("[CALL] TURN unavailable, using STUN only", error);
            return [...defaultIceServers];
        }
    }

    async function createPeerConnection() {
        const iceServers = await getCallIceServers();
        state.pc = new RTCPeerConnection({
            iceServers,
            iceCandidatePoolSize: 10,
            iceTransportPolicy: "all",
            bundlePolicy: "max-bundle",
            rtcpMuxPolicy: "require",
        });
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

        const audioTransceiver = state.pc.addTransceiver("audio", {
            direction: "sendrecv",
        });
        state.audioSender = audioTransceiver.sender;
        const audioTrack = state.localStream.getAudioTracks()[0];
        if (audioTrack) await state.audioSender.replaceTrack(audioTrack);

        const videoTransceiver = state.pc.addTransceiver("video", {
            direction: "sendrecv",
        });
        state.videoSender = videoTransceiver.sender;
        state.pc.ontrack = (event) => {
            const tracks = event.streams?.[0]?.getTracks?.().length
                ? event.streams[0].getTracks()
                : [event.track];
            tracks.forEach((track) => addRemoteTrack(track));
            playRemoteMedia();
            updateRemotePreview();
            if (!state.remoteMeterStarted && state.remoteStream.getAudioTracks().length) {
                state.remoteMeterStarted = true;
                startVolumeMeter(state.remoteStream, "remoteCallTile");
            }
        };
        state.pc.onicecandidate = (event) => {
            if (event.candidate)
                send("call_signal", {
                    to: state.peerId,
                    signal_type: "candidate",
                    candidate: event.candidate,
                });
        };
        state.pc.onconnectionstatechange = () => {
            const pill = $("callStatusPill");
            if (pill)
                pill.textContent =
                    state.pc.connectionState === "connected"
                        ? "В звонке"
                        : `Статус: ${state.pc.connectionState}`;
        };
    }

    async function createOffer() {
        const offer = await state.pc.createOffer();
        await state.pc.setLocalDescription(offer);
        send("call_signal", {
            to: state.peerId,
            signal_type: "offer",
            sdp: state.pc.localDescription,
        });
    }

    async function handleOffer(sdp, from) {
        if (!state.inCall) {
            state.peerId = from;
            state.peerName = callPeer()?.nickname || "Собеседник";
            await openCall(false);
        }
        await state.pc.setRemoteDescription(sdp);
        await flushCandidates();
        const answer = await state.pc.createAnswer();
        await state.pc.setLocalDescription(answer);
        send("call_signal", {
            to: state.peerId,
            signal_type: "answer",
            sdp: state.pc.localDescription,
        });
    }

    async function handleAnswer(sdp) {
        await state.pc?.setRemoteDescription(sdp);
        await flushCandidates();
    }

    async function handleCandidate(candidate) {
        if (!state.pc?.remoteDescription) {
            state.pendingCandidates.push(candidate);
            return;
        }
        await state.pc.addIceCandidate(candidate);
    }

    async function flushCandidates() {
        while (state.pendingCandidates.length && state.pc?.remoteDescription) {
            await state.pc.addIceCandidate(state.pendingCandidates.shift());
        }
    }

    function bindControls() {
        $("callMicToggle").onclick = toggleMic;
        $("callCameraToggle").onclick = toggleCamera;
        $("callScreenToggle").onclick = toggleScreen;
        $("callSettingsToggle").onclick = () =>
            $("callOverlay").classList.toggle("call-settings-panel-hidden");
        $("callEndButton").onclick = () => endCall(true, "Звонок завершён");
        $("callMicVolume").oninput = (e) => {
            state.micVolume = Number(e.target.value) / 100;
            $("callMicVolumeText").textContent = `${e.target.value}%`;
        };
        $("callSpeakerVolume").oninput = (e) => {
            state.speakerVolume = Number(e.target.value) / 100;
            const remote = $("remoteCallAudio");
            if (remote) remote.volume = state.speakerVolume;
            $("callSpeakerVolumeText").textContent = `${e.target.value}%`;
        };
        $("callNoiseSuppression").onchange = async (e) => {
            state.noiseSuppression = e.target.checked;
            await setupLocalMedia();
        };
        $("callMicSelect").onchange = async (e) => {
            state.selectedMicId = e.target.value;
            await setupLocalMedia();
        };
        $("callCameraSelect").onchange = async (e) => {
            state.selectedCameraId = e.target.value;
            if (state.cameraEnabled) await restartCamera();
        };
        $("callSpeakerSelect").onchange = async (e) => {
            state.selectedSpeakerId = e.target.value;
            await setSpeakerSink();
        };
        $("callRefreshDevices").onclick = async () => {
            await requestCallMediaPermissions();
        };
        $("callTestMic").onclick = () =>
            toast(
                "Проверка микрофона",
                "Говорите — индикатор рядом показывает входящий уровень.",
            );
        $("callPickSpeaker").onclick = pickSpeakerOutput;
        $("callTestSpeaker").onclick = testSpeaker;
        $("callTestCamera").onclick = async () => {
            if (!state.cameraEnabled) await toggleCamera();
            toast(
                "Проверка камеры",
                "Если вы видите себя в плитке — камера работает.",
            );
        };
    }

    function toggleMic() {
        state.micEnabled = !state.micEnabled;
        state.localStream
            ?.getAudioTracks()
            .forEach((t) => (t.enabled = state.micEnabled));
        $("callMicToggle").classList.toggle("disabled", !state.micEnabled);
        $("localCallState").textContent = state.micEnabled
            ? "Микрофон включён"
            : "Микрофон выключен";
        send("call_state", {
            to: state.peerId,
            micEnabled: state.micEnabled,
            cameraEnabled: state.cameraEnabled,
            screenEnabled: state.screenEnabled,
        });
    }

    async function toggleCamera() {
        state.cameraEnabled = !state.cameraEnabled;
        $("callCameraToggle").classList.toggle("active", state.cameraEnabled);
        $("callCameraToggle").classList.toggle(
            "disabled",
            !state.cameraEnabled,
        );
        if (state.cameraEnabled) await restartCamera();
        else {
            const old = state.localStream?.getVideoTracks()[0];
            if (old) old.stop();
            state.localStream?.removeTrack(old);
            if (!state.screenEnabled) await replaceTrack("video", null);
        }
        if (!state.screenEnabled) updateLocalPreview();
        send("call_state", {
            to: state.peerId,
            micEnabled: state.micEnabled,
            cameraEnabled: state.cameraEnabled,
            screenEnabled: state.screenEnabled,
        });
    }

    async function restartCamera() {
        const deviceId = $("callCameraSelect")?.value;
        const cameraStream = await navigator.mediaDevices.getUserMedia({
            video: videoConstraints(),
            audio: false,
        });
        const newTrack = cameraStream.getVideoTracks()[0];
        const old = state.localStream.getVideoTracks()[0];
        if (old) {
            old.stop();
            state.localStream.removeTrack(old);
        }
        state.localStream.addTrack(newTrack);
        if (!state.screenEnabled) {
            await replaceTrack("video", newTrack);
            updateLocalPreview();
        }
    }

    async function toggleScreen() {
        if (!state.screenEnabled) {
            state.screenStream = await navigator.mediaDevices.getDisplayMedia({
                video: true,
                audio: false,
            });
            const track = state.screenStream.getVideoTracks()[0];
            track.onended = () => {
                if (state.screenEnabled) toggleScreen();
            };
            state.screenEnabled = true;
            await replaceTrack("video", track);
            setVideo("localCallVideo", new MediaStream([track]), true);
            setTileVideoState("local", true);
            $("localCallState").textContent = "Показываете экран";
        } else {
            stopTracks(state.screenStream);
            state.screenStream = null;
            state.screenEnabled = false;
            if (state.cameraEnabled) await restartCamera();
            else await replaceTrack("video", null);
            updateLocalPreview();
        }
        $("callScreenToggle").classList.toggle("active", state.screenEnabled);
        send("call_state", {
            to: state.peerId,
            micEnabled: state.micEnabled,
            cameraEnabled: state.cameraEnabled,
            screenEnabled: state.screenEnabled,
        });
    }

    async function replaceTrack(kind, track) {
        if (!state.pc) return;
        const sender = kind === "audio" ? state.audioSender : state.videoSender;
        if (sender) {
            await sender.replaceTrack(track || null);
            return;
        }
        if (track) {
            const stream =
                kind === "video" && state.screenEnabled
                    ? state.screenStream
                    : state.localStream;
            const newSender = state.pc.addTrack(
                track,
                stream || state.localStream,
            );
            if (kind === "audio") state.audioSender = newSender;
            if (kind === "video") state.videoSender = newSender;
        }
    }

    function updateLocalPreview() {
        const videoTrack = state.localStream?.getVideoTracks()[0];
        if (videoTrack)
            setVideo("localCallVideo", new MediaStream([videoTrack]), true);
        setTileVideoState("local", Boolean(videoTrack));
    }

    function addRemoteTrack(track) {
        const targetStream =
            track.kind === "audio" ? state.remoteAudioStream : state.remoteVideoStream;
        [state.remoteStream, targetStream].forEach((stream) => {
            if (stream && !stream.getTracks().some((t) => t.id === track.id)) {
                stream.addTrack(track);
            }
        });
        track.onunmute = () => {
            playRemoteMedia();
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

    function playRemoteMedia() {
        const remoteAudio = $("remoteCallAudio");
        if (remoteAudio) {
            remoteAudio.srcObject = state.remoteAudioStream;
            remoteAudio.volume = state.speakerVolume;
            remoteAudio.play?.().catch((error) =>
                console.warn("[CALL] remote audio autoplay blocked", error),
            );
        }
        const remoteVideo = $("remoteCallVideo");
        if (remoteVideo) {
            remoteVideo.srcObject = state.remoteVideoStream;
            remoteVideo.muted = true;
            remoteVideo.play?.().catch(() => {});
        }
    }

    function updateRemotePreview() {
        const hasLiveVideo = state.remoteVideoStream
            ?.getVideoTracks()
            .some((track) => track.readyState === "live" && track.enabled);
        const hasLiveAudio = state.remoteAudioStream
            ?.getAudioTracks()
            .some((track) => track.readyState === "live" && track.enabled);
        setTileVideoState("remote", Boolean(hasLiveVideo));
        if ($("remoteCallState")) {
            $("remoteCallState").textContent = hasLiveVideo
                ? "Видео подключено"
                : hasLiveAudio
                  ? "Аудио подключено"
                  : "Ожидаем медиа";
        }
    }

    function setVideo(id, stream, muted) {
        const video = $(id);
        if (!video) return;
        video.srcObject = stream;
        video.muted = muted;
        video.play?.().catch(() => {});
    }

    function setTileVideoState(kind, hasVideo) {
        $(`${kind}CallVideo`)?.classList.toggle("is-hidden", !hasVideo);
        const empty = $(`${kind}CallEmpty`);
        if (empty) empty.style.display = hasVideo ? "none" : "grid";
    }

    async function loadDevices() {
        if (!navigator.mediaDevices?.enumerateDevices) return;
        try {
            const devices = await navigator.mediaDevices.enumerateDevices();
            fillSelect(
                "callMicSelect",
                devices.filter((d) => d.kind === "audioinput"),
                "Микрофон",
                state.selectedMicId,
                (value) => (state.selectedMicId = value),
            );
            fillSelect(
                "callSpeakerSelect",
                devices.filter((d) => d.kind === "audiooutput"),
                "Динамик",
                state.selectedSpeakerId,
                (value) => (state.selectedSpeakerId = value),
            );
            fillSelect(
                "callCameraSelect",
                devices.filter((d) => d.kind === "videoinput"),
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
        const options = [
            `<option value="">${fallback} по умолчанию</option>`,
            ...devices.map(
                (d, i) =>
                    `<option value="${safeAttr(d.deviceId)}">${safe(d.label || `${fallback} ${i + 1}`)}</option>`,
            ),
        ];
        select.innerHTML = options.join("");
        if (previous && devices.some((d) => d.deviceId === previous)) {
            select.value = previous;
        }
        onSelected?.(select.value);
    }

    async function setSpeakerSink() {
        const remote = $("remoteCallAudio");
        const deviceId = $("callSpeakerSelect")?.value || "";
        if (!remote?.setSinkId) {
            if ($("callSpeakerSelect")) $("callSpeakerSelect").disabled = true;
            return;
        }
        try {
            await remote.setSinkId(deviceId);
            setDeviceHint(
                "callSpeakerPermissionStatus",
                deviceId
                    ? "Выбран отдельный динамик для звонка"
                    : "Будет использован системный динамик по умолчанию",
            );
        } catch (error) {
            console.warn("[CALL] setSinkId failed", error);
        }
    }

    async function pickSpeakerOutput() {
        if (!navigator.mediaDevices?.selectAudioOutput) {
            toast(
                "Выбор динамика недоступен",
                "Ваш браузер не поддерживает отдельное окно выбора устройства вывода. Используется системный динамик по умолчанию.",
                "danger",
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
            setDeviceHint(
                "callSpeakerPermissionStatus",
                `Динамик выбран: ${device.label || "устройство вывода"}`,
            );
        } catch (error) {
            console.warn("[CALL] selectAudioOutput failed", error);
            setDeviceHint(
                "callSpeakerPermissionStatus",
                "Выбор динамика отменён или запрещён браузером.",
                true,
            );
        }
    }

    function subscribeDeviceChanges() {
        if (!navigator.mediaDevices || state.deviceChangeHandler) return;
        state.deviceChangeHandler = () => loadDevices();
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

    function testSpeaker() {
        const ctx = new AudioContext();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        const destination = ctx.createMediaStreamDestination();
        const audio = new Audio();
        osc.frequency.value = 660;
        gain.gain.value = state.speakerVolume * 0.18;
        osc.connect(gain).connect(destination);
        audio.srcObject = destination.stream;
        audio.volume = state.speakerVolume;
        const deviceId = $("callSpeakerSelect")?.value || "";
        if (audio.setSinkId) audio.setSinkId(deviceId).catch(() => {});
        audio.play().catch(() => {});
        osc.start();
        setTimeout(() => {
            osc.stop();
            audio.pause();
            ctx.close();
        }, 450);
        const meter = $("callSpeakerMeter");
        if (meter) {
            meter.style.width = "100%";
            setTimeout(() => (meter.style.width = "0%"), 500);
        }
    }

    function startVolumeMeter(stream, tileId, onSpeakingChange) {
        if (!stream?.getAudioTracks().length) return;
        const ctx = new AudioContext();
        const analyser = ctx.createAnalyser();
        analyser.fftSize = 512;
        ctx.createMediaStreamSource(stream).connect(analyser);
        const data = new Uint8Array(analyser.frequencyBinCount);
        let lastSpeaking = false;
        let lastSent = 0;
        const tick = () => {
            if (!state.inCall) {
                ctx.close();
                return;
            }
            analyser.getByteFrequencyData(data);
            const avg = data.reduce((a, b) => a + b, 0) / data.length;
            const speaking = avg > 18;
            $(tileId)?.classList.toggle("speaking", speaking);
            if (tileId === "localCallTile")
                $("callMicMeter") &&
                    ($("callMicMeter").style.width =
                        `${Math.min(100, avg * 2.5)}%`);
            if (speaking !== lastSpeaking && Date.now() - lastSent > 220) {
                lastSpeaking = speaking;
                lastSent = Date.now();
                onSpeakingChange?.(speaking);
            }
            requestAnimationFrame(tick);
        };
        tick();
        state.meters.push(ctx);
    }

    function stopTracks(stream) {
        stream?.getTracks?.().forEach((track) => track.stop());
    }

    function endCall(notifyPeer, reason = "Звонок завершён") {
        if (notifyPeer && state.peerId)
            send("call_ended", { to: state.peerId, reason });
        cleanupActiveCall(false);
        addChatSystemMessage(reason);
    }

    function cleanupActiveCall(showMessage = true) {
        state.inCall = false;
        unsubscribeDeviceChanges();
        state.pc?.close?.();
        stopTracks(state.localStream);
        stopTracks(state.rawMicStream);
        stopTracks(state.remoteStream);
        stopTracks(state.screenStream);
        state.meters.forEach((ctx) => ctx.close?.());
        state.micContext?.close?.();
        document
            .querySelectorAll(".call-overlay,.incoming-call-overlay")
            .forEach((n) => n.remove());
        Object.assign(state, {
            pc: null,
            localStream: null,
            remoteStream: null,
            remoteAudioStream: null,
            remoteVideoStream: null,
            screenStream: null,
            micContext: null,
            micGain: null,
            micDestination: null,
            rawMicStream: null,
            inCall: false,
            cameraEnabled: false,
            screenEnabled: false,
            pendingCandidates: [],
            meters: [],
            audioSender: null,
            videoSender: null,
            remoteMeterStarted: false,
        });
        if (showMessage)
            addChatSystemMessage("Звонок завершён. Вы вернулись в чат.");
    }

    window.startOutgoingCall = async function () {
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
        await requestInitialMediaPermissions();
        send("call_request", { to: state.peerId });
        toast("Исходящий звонок", `Звоним пользователю ${state.peerName}...`);
    };

    window.handleCallMessage = async function (msg) {
        const data = msg.data || {};
        try {
            if (msg.type === "call_request") {
                createIncomingModal(
                    data.from_nickname || callPeer()?.nickname || "Собеседник",
                    data.from,
                );
            } else if (msg.type === "call_response") {
                if (!data.accepted) {
                    addChatSystemMessage("Собеседник сбросил звонок.");
                    toast(
                        "Звонок сброшен",
                        "Собеседник отклонил вызов.",
                        "danger",
                    );
                } else {
                    await openCall(true);
                }
            } else if (msg.type === "call_signal") {
                if (data.signal_type === "offer")
                    await handleOffer(data.sdp, data.from);
                if (data.signal_type === "answer") await handleAnswer(data.sdp);
                if (data.signal_type === "candidate")
                    await handleCandidate(data.candidate);
            } else if (msg.type === "call_ended") {
                cleanupActiveCall(false);
                addChatSystemMessage(
                    data.reason || "Собеседник завершил звонок.",
                );
            } else if (msg.type === "call_state") {
                $("remoteCallTile")?.classList.toggle(
                    "speaking",
                    Boolean(data.speaking),
                );
                if ($("remoteCallState")) {
                    $("remoteCallState").textContent = data.screenEnabled
                        ? "Показывает экран"
                        : data.cameraEnabled
                          ? "Камера включена"
                          : data.micEnabled === false
                            ? "Микрофон выключен"
                            : "В разговоре";
                }
            }
        } catch (error) {
            console.error("[CALL] handleCallMessage failed", error);
            toast(
                "Ошибка звонка",
                error.message || "Не удалось обработать событие звонка",
                "danger",
            );
        }
    };

    window.cleanupActiveCall = cleanupActiveCall;
})();
