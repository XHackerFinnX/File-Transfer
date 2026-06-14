// P2P Chat with E2E Encryption + Optimized File Transfer (binary with fallback)
let pc,
    dataChannel,
    fileChannel,
    myKeyPair,
    sharedKey,
    myPublicKeyBase64,
    peerPublicKeyBase64,
    peerId,
    peerNickname,
    messages = [];
let connectionTimeout = null;
let connectionTimerInterval = null;
let connectionStartedAt = 0;
let keyExchangeStartedAt = 0;

let connectionAttempts = 0;
const MAX_TURN_ATTEMPTS = 2;
let forceTurn = true;
let usingTurn = false;
let localTransportState = null;
let remoteTransportState = null;
let lastLocalTransportSignature = "";
let lastRemoteTransportSignature = "";
let pendingRemoteCandidates = [];
let turnProbePromise = null;
let turnProbeCachedAt = 0;
let turnProbeCachedResult = false;
const TURN_PROBE_CACHE_MS = 60_000;
const PLAN_B_ACTIVATION_DELAY_MS = 20_000;
const CONNECTION_WARNING_DELAY_MS = 30_000;
const KEY_RETRY_INTERVAL_MS = 2_000;
const DEFAULT_STUN_SERVERS = [
    { urls: "stun:stun.l.google.com:19302" },
    { urls: "stun:stun1.l.google.com:19302" },
    { urls: "stun:stun2.l.google.com:19302" },
    { urls: "stun:stun.cloudflare.com:3478" },
];
let localReadySent = false;
let remoteReadyReceived = false;
let connectionReadyShown = false;
let planBActive = false;
let planBTimer = null;
let rtcState = {
    iceConnected: false,
    dataOpen: false,
    keyReady: false,
};

let pendingPlanBKeyRetry = null;
let typingStopTimeout = null;
let peerActivityTimeout = null;
const pendingReadReceipts = new Set();
let safetyNumber = null;
let safetyVerified = true;

function updateSafetyVerificationUI() {
    // Ручное подтверждение по коду отключено: после обмена ключами чат готов автоматически.
}

function formatElapsedTime(ms) {
    const totalSeconds = Math.max(0, Math.floor(ms / 1000));
    const minutes = Math.floor(totalSeconds / 60)
        .toString()
        .padStart(2, "0");
    const seconds = (totalSeconds % 60).toString().padStart(2, "0");
    return `${minutes}:${seconds}`;
}

function buildConnectionTimerText(baseText) {
    const now = Date.now();
    const connectionElapsed = connectionStartedAt
        ? formatElapsedTime(now - connectionStartedAt)
        : "00:00";
    const keyElapsed = keyExchangeStartedAt
        ? formatElapsedTime(now - keyExchangeStartedAt)
        : "00:00";
    return `${baseText}\nТаймер соединения: ${connectionElapsed} • Обмен ключами: ${keyElapsed}`;
}

function updateConnectionTimerText(
    baseText = "Устанавливаем защищённое соединение...",
) {
    const text = buildConnectionTimerText(baseText);
    const connectingText = document.getElementById("connectingText");
    if (connectingText) connectingText.textContent = text;
    const overlay = document.getElementById("connectionOverlay");
    const overlayText = overlay?.querySelector(".connection-overlay-text");
    if (overlayText && !overlay.classList.contains("hidden")) {
        overlayText.textContent = text;
    }
}

function startConnectionTimers(
    baseText = "Устанавливаем защищённое соединение...",
) {
    connectionStartedAt = Date.now();
    keyExchangeStartedAt = Date.now();
    clearInterval(connectionTimerInterval);
    updateConnectionTimerText(baseText);
    connectionTimerInterval = setInterval(() => {
        updateConnectionTimerText(baseText);
        if (!sharedKey && peerId) {
            requestPeerPublicKey("key_timer_retry");
        }
    }, KEY_RETRY_INTERVAL_MS);
}

function stopConnectionTimers() {
    clearInterval(connectionTimerInterval);
    connectionTimerInterval = null;
}

function uint8ArrayToBase64(bytes) {
    let binary = "";
    const chunkSize = 0x8000;
    for (let i = 0; i < bytes.length; i += chunkSize) {
        binary += String.fromCharCode(...bytes.subarray(i, i + chunkSize));
    }
    return btoa(binary);
}

function base64ToUint8Array(base64) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
}

function requestPeerPublicKey(reason = "manual") {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    if (!peerId) return;
    if (!myKeyPair) return;
    ws.send(
        JSON.stringify({
            type: "public_key_request",
            data: { to: peerId },
        }),
    );
    console.log(`[KEY] public_key_request отправлен (${reason})`);
}

async function sha256Hex(input) {
    const data = new TextEncoder().encode(input);
    const hash = await crypto.subtle.digest("SHA-256", data);
    return Array.from(new Uint8Array(hash))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
}

async function computeSafetyNumber() {
    if (!myPublicKeyBase64 || !peerPublicKeyBase64) return null;
    const [a, b] = [myPublicKeyBase64, peerPublicKeyBase64].sort();
    const digestHex = await sha256Hex(`${a}:${b}`);
    return digestHex
        .slice(0, 24)
        .toUpperCase()
        .match(/.{1,4}/g)
        .join("-");
}

function showSafetyNumberPrompt() {
    addSystemMessage(
        "Ручное подтверждение по коду отключено. Шифрование включается автоматически после обмена ключами.",
    );
}

function canSendEncryptedPayload() {
    if (!sharedKey) {
        updateChatStatus("Ключи шифрования ещё не согласованы");
        return false;
    }
    return true;
}

async function encryptPlanBPayload(payload) {
    if (!canSendEncryptedPayload()) return null;
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const encoded = new TextEncoder().encode(JSON.stringify(payload));
    const encrypted = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv },
        sharedKey,
        encoded,
    );
    return {
        mode: "encrypted_payload",
        iv: Array.from(iv),
        encrypted: uint8ArrayToBase64(new Uint8Array(encrypted)),
    };
}

async function decryptPlanBPayload(payload) {
    if (!sharedKey) {
        requestPeerPublicKey("relay_message_without_key");
        return null;
    }
    const iv = new Uint8Array(payload.iv);
    const encrypted = base64ToUint8Array(payload.encrypted);
    const decrypted = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv },
        sharedKey,
        encrypted,
    );
    return JSON.parse(new TextDecoder().decode(decrypted));
}

function setConnectionOverlayVisible(visible, text) {
    const overlay = document.getElementById("connectionOverlay");
    if (!overlay) return;

    if (text) {
        const textEl = overlay.querySelector(".connection-overlay-text");
        if (textEl) textEl.textContent = text;
    }
    overlay.classList.toggle("hidden", !visible);
}

function resetConnectionReadiness() {
    clearTimeout(planBTimer);
    planBTimer = null;
    stopConnectionTimers();
    clearTimeout(pendingPlanBKeyRetry);
    pendingPlanBKeyRetry = null;
    localReadySent = false;
    remoteReadyReceived = false;
    connectionReadyShown = false;
    planBActive = false;
    rtcState = {
        iceConnected: false,
        dataOpen: false,
        keyReady: false,
    };
    localTransportState = null;
    remoteTransportState = null;
    lastLocalTransportSignature = "";
    lastRemoteTransportSignature = "";
    planBReceivingFiles = {};
    planBOutgoingFileTransfer = null;
    clearTimeout(typingStopTimeout);
    clearTimeout(peerActivityTimeout);
    pendingReadReceipts.clear();
    safetyNumber = null;
    safetyVerified = true;
    myPublicKeyBase64 = null;
    peerPublicKeyBase64 = null;
    updateSafetyVerificationUI();
}

function buildTransportState(overrides = {}) {
    const baseMode = planBActive
        ? "plan_b"
        : usingTurn
          ? "turn+stun"
          : "stun_only";
    return {
        mode: baseMode,
        planBActive,
        usingTurn,
        iceState: pc?.iceConnectionState || "new",
        dataChannelState: dataChannel?.readyState || "closed",
        ...overrides,
    };
}

function transportSignature(state) {
    if (!state) return "";
    return `${state.mode}|${state.planBActive}|${state.usingTurn}|${state.iceState}|${state.dataChannelState}`;
}

function transportLabel(state) {
    if (!state) return "неизвестно";
    if (state.mode === "plan_b") return "Plan B";
    if (state.mode === "turn+stun") return "P2P TURN+STUN";
    return "P2P STUN-only";
}

function announceTransportState(state, isLocal = true) {
    const signature = transportSignature(state);
    if (isLocal) {
        if (signature === lastLocalTransportSignature) return;
        lastLocalTransportSignature = signature;
        // addSystemMessage(`Вы используете: ${transportLabel(state)}`);
        return;
    }

    if (signature === lastRemoteTransportSignature) return;
    lastRemoteTransportSignature = signature;
    const name = peerNickname || "Собеседник";
    // addSystemMessage(`${name} использует: ${transportLabel(state)}`);
}

function syncTransportState(reason = "state_update", announce = true) {
    localTransportState = buildTransportState();
    if (announce) {
        announceTransportState(localTransportState, true);
    }
    if (!ws || ws.readyState !== WebSocket.OPEN || !peerId) return;

    ws.send(
        JSON.stringify({
            type: "transport_state",
            data: { to: peerId, reason, state: localTransportState },
        }),
    );
}

function showPlanBReadyState(
    statusText = "Plan B: серверный защищённый канал активен",
) {
    clearTimeout(connectionTimeout);
    clearTimeout(planBTimer);
    planBTimer = null;
    if (sharedKey) stopConnectionTimers();
    setConnectionOverlayVisible(false);
    if (typeof window.setChatScreen === "function") {
        window.setChatScreen("chat");
    }
    updateChatStatus(statusText);
}

function evaluateConnectionReady() {
    if (planBActive) {
        showPlanBReadyState();
        return;
    }

    const localReady =
        rtcState.iceConnected && rtcState.dataOpen && rtcState.keyReady;
    const fullyReady = localReady && localReadySent && remoteReadyReceived;

    if (fullyReady) {
        planBActive = false;
        setConnectionOverlayVisible(false);
        if (typeof window.setChatScreen === "function") {
            window.setChatScreen("chat");
        }
        updateChatStatus("");
        syncTransportState("p2p_ready");
        if (!connectionReadyShown) {
            addSystemMessage(
                "Защищённое соединение установлено у обоих собеседников",
            );
            connectionReadyShown = true;
        }
    } else {
        const waitText = localReady
            ? "Ваше соединение готово. Ждём подтверждение готовности собеседника..."
            : "Устанавливаем защищённое соединение...";
        const timedWaitText = buildConnectionTimerText(waitText);
        if (typeof window.setChatScreen === "function") {
            window.setChatScreen("connecting", timedWaitText);
        }
        setConnectionOverlayVisible(true, timedWaitText);
    }
}

function activatePlanB() {
    if (connectionReadyShown) return;
    if (planBActive) {
        showPlanBReadyState();
        return;
    }
    planBActive = true;
    showPlanBReadyState();
    addSystemMessage("P2P не установился вовремя — переключились на Plan B.");
    syncTransportState("plan_b_activated");
    if (!sharedKey) {
        requestPeerPublicKey("plan_b_activated");
        clearTimeout(pendingPlanBKeyRetry);
        pendingPlanBKeyRetry = setTimeout(() => {
            if (!sharedKey && planBActive) {
                requestPeerPublicKey("plan_b_retry");
            }
        }, 1500);
    }
}

function activatePlanBFromRemote() {
    if (planBActive) {
        showPlanBReadyState("Plan B: серверный защищённый канал активен");
        return;
    }
    planBActive = true;
    showPlanBReadyState("Plan B: собеседник переключился на серверный канал");
    addSystemMessage(
        "Собеседник переключился на Plan B — продолжаем чат через серверный канал.",
    );
    syncTransportState("plan_b_detected_from_remote");
    if (!sharedKey) {
        requestPeerPublicKey("plan_b_from_remote");
    }
}

function trySendReady() {
    if (localReadySent || !dataChannel || dataChannel.readyState !== "open") {
        return;
    }
    if (!(rtcState.iceConnected && rtcState.keyReady && rtcState.dataOpen)) {
        return;
    }

    try {
        dataChannel.send(JSON.stringify({ type: "ready" }));
        localReadySent = true;
        evaluateConnectionReady();
    } catch (err) {
        console.warn("[READY] Не удалось отправить ready:", err);
    }
}

async function probeTurnRelay(iceServers, timeoutMs = 8000) {
    let pc = null;
    try {
        pc = new RTCPeerConnection({
            iceServers,
            iceTransportPolicy: "relay",
        });
        pc.createDataChannel("turn-probe");

        return await new Promise(async (resolve) => {
            let finished = false;
            const finish = (ok) => {
                if (finished) return;
                finished = true;
                resolve(ok);
            };

            const timer = setTimeout(() => finish(false), timeoutMs);

            pc.onicecandidate = (event) => {
                if (!event.candidate) {
                    clearTimeout(timer);
                    finish(false);
                    return;
                }
                const candidateText = event.candidate.candidate || "";
                if (candidateText.includes(" typ relay ")) {
                    clearTimeout(timer);
                    finish(true);
                }
            };

            pc.onicegatheringstatechange = () => {
                if (pc.iceGatheringState === "complete") {
                    clearTimeout(timer);
                    finish(false);
                }
            };

            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
        });
    } catch (err) {
        console.warn("[TURN] Ошибка проверки relay-кандидата:", err);
        return false;
    } finally {
        if (pc) pc.close();
    }
}

async function checkTurnAvailability(iceServers) {
    const now = Date.now();
    if (now - turnProbeCachedAt < TURN_PROBE_CACHE_MS) {
        return turnProbeCachedResult;
    }
    if (turnProbePromise) {
        return turnProbePromise;
    }

    turnProbePromise = (async () => {
        const ok = await probeTurnRelay(iceServers);
        turnProbeCachedResult = ok;
        turnProbeCachedAt = Date.now();
        turnProbePromise = null;
        return ok;
    })();

    return turnProbePromise;
}

// Для бинарной передачи
let currentFileTransfer = null;
let fileTransferProgressElement = null;
let nextFileId = 1;
let receivingFiles = {};
let binaryFileSupported = false;
let fileChannelReady = false;
const MAX_TRANSFER_FILE_SIZE = 100 * 1024 * 1024 * 1024; // 100 GB
const BINARY_FILE_CHUNK_SIZE = 256 * 1024;
const BINARY_ACK_EVERY_CHUNKS = 32;
const BINARY_MAX_UNACKED_BYTES = 256 * 1024 * 1024;
const FILE_CHANNEL_BUFFER_HIGH_WATERMARK = 64 * 1024 * 1024;
const PLAN_B_FILE_CHUNK_SIZE = 256 * 1024;
const PLAN_B_MAX_FILE_SIZE = MAX_TRANSFER_FILE_SIZE;
const PLAN_B_ACK_EVERY_CHUNKS = 32;
const PLAN_B_MAX_IN_FLIGHT = 256;
const PLAN_B_ACK_TIMEOUT_MS = 15000;
const FILE_STALL_CHECK_INTERVAL_MS = 5000;
let planBReceivingFiles = {};
let planBOutgoingFileTransfer = null;

function removeFileTransferProgressElement() {
    document
        .querySelectorAll(".file-progress-container")
        .forEach((element) => element.remove());
    fileTransferProgressElement = null;
}

function createFileTransferProgressElement(cancelHandler) {
    removeFileTransferProgressElement();
    fileTransferProgressElement = document.createElement("div");
    fileTransferProgressElement.className = "file-progress-container";
    fileTransferProgressElement.innerHTML = `
        <div class="file-progress-bar"><div class="file-progress-fill"></div></div>
        <div class="file-progress-info">0%</div>
        <button class="file-progress-cancel" type="button" aria-label="Отменить передачу">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
        </button>
    `;
    const inputWrapper = document.querySelector(".message-input-wrapper");
    if (inputWrapper) inputWrapper.before(fileTransferProgressElement);
    fileTransferProgressElement
        .querySelector(".file-progress-cancel")
        .addEventListener("click", cancelHandler);
    return fileTransferProgressElement;
}

function updateFileTransferProgress(fillPercent, infoText) {
    if (!fileTransferProgressElement) return;
    const progressFill = fileTransferProgressElement.querySelector(
        ".file-progress-fill",
    );
    const progressInfo = fileTransferProgressElement.querySelector(
        ".file-progress-info",
    );
    if (progressFill)
        progressFill.style.width = `${Math.min(100, Math.max(0, fillPercent))}%`;
    if (progressInfo) progressInfo.textContent = infoText;
}

function cancelActiveFileTransfer() {
    if (
        currentFileTransfer &&
        typeof currentFileTransfer.cancel === "function"
    ) {
        currentFileTransfer.cancel();
        return true;
    }
    removeFileTransferProgressElement();
    return false;
}

function isTransferCancellationError(err) {
    return err && /cancelled|canceled/i.test(err.message || "");
}

window.prepareWaitingRoom = function (roomData = {}) {
    clearTimeout(connectionTimeout);
    resetConnectionReadiness();
    setConnectionOverlayVisible(false);
    peerId = null;
    peerNickname = null;
    if (dataChannel) dataChannel.close();
    if (fileChannel) fileChannel.close();
    if (pc) pc.close();
    pc = null;
    dataChannel = null;
    fileChannel = null;
    messages = [];
    removeFileTransferProgressElement();
    currentFileTransfer = null;
    updateChatStatus("Ожидаем подключения другого пользователя...");
    const messagesDiv = document.getElementById("messages");
    if (messagesDiv) messagesDiv.innerHTML = "";
    addSystemMessage(
        `Комната «${roomData.title || "Мой чат"}» создана. Ожидаем собеседника...`,
    );
};

async function getIceServers() {
    try {
        usingTurn = false;
        if (!forceTurn) {
            console.warn("⚠️ Используем только STUN (fallback)");
            return [...DEFAULT_STUN_SERVERS];
        }

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3000);

        const res = await fetch("/turn-credentials", {
            signal: controller.signal,
        });

        clearTimeout(timeoutId);

        if (!res.ok) throw new Error(`TURN error ${res.status}`);

        const { username, credential, urls } = await res.json();
        const turnIceServers = [{ urls, username, credential }];
        const turnReady = await checkTurnAvailability(turnIceServers);

        if (!turnReady) {
            console.warn(
                "⚠️ TURN relay не подтвердился быстро, но оставляем TURN в ICE для медленных/VPN сетей",
            );
            usingTurn = true;
            return [...turnIceServers, ...DEFAULT_STUN_SERVERS];
        }

        usingTurn = true;
        console.log("✅ TURN доступен: запускаем ICE с TURN + STUN");
        syncTransportState("turn_and_stun_selected", false);
        return [...turnIceServers, ...DEFAULT_STUN_SERVERS];
    } catch (err) {
        console.warn("❌ TURN недоступен → fallback на STUN");
        usingTurn = false;
        syncTransportState("stun_only_selected", false);

        return [...DEFAULT_STUN_SERVERS];
    }
}

function isImageFile(mimeType) {
    return mimeType.startsWith("image/");
}

function getTransferSpeedLabel(startedAt, transferredBytes) {
    const elapsedSec = (Date.now() - startedAt) / 1000;
    if (elapsedSec <= 0) return "0.00 MB/с";
    const mbPerSec = transferredBytes / 1024 / 1024 / elapsedSec;
    return `${mbPerSec.toFixed(2)} MB/с`;
}

function setPeerHeaderStatus(text) {
    const statusEl = document.getElementById("chatPeerStatusText");
    if (statusEl) statusEl.textContent = text || "В сети";
}

function canMarkMessagesAsRead() {
    const chat = document.getElementById("chat");
    return !document.hidden && chat && chat.style.display === "flex";
}

async function sendRealtimeSignal(payload) {
    if (planBActive) {
        if (!ws || ws.readyState !== WebSocket.OPEN || !peerId) return;
        const relayPayload = await encryptPlanBPayload(payload);
        if (!relayPayload) return;
        ws.send(
            JSON.stringify({
                type: "relay_message",
                data: { to: peerId, payload: relayPayload },
            }),
        );
        return;
    }
    if (!dataChannel || dataChannel.readyState !== "open") return;
    dataChannel.send(JSON.stringify(payload));
}

function flushReadReceipts() {
    if (!canMarkMessagesAsRead() || pendingReadReceipts.size === 0) return;
    for (const messageId of [...pendingReadReceipts]) {
        void sendRealtimeSignal({ type: "read_receipt", messageId });
        pendingReadReceipts.delete(messageId);
    }
}

function queueReadReceipt(messageId) {
    if (!messageId) return;
    pendingReadReceipts.add(messageId);
    flushReadReceipts();
}

function markMessageAsRead(messageId) {
    const message = messages.find((m) => m.id === messageId && m.from === "me");
    if (!message) return;
    message.read = true;
    renderMessages();
}

function showPeerActivityStatus(activity) {
    const statusByActivity = {
        typing: `${peerNickname || "Собеседник"} печатает...`,
        sending_file: `${peerNickname || "Собеседник"} отправляет файл...`,
        sending_image: `${peerNickname || "Собеседник"} отправляет изображение...`,
    };
    setPeerHeaderStatus(statusByActivity[activity] || "В сети");
    clearTimeout(peerActivityTimeout);
    peerActivityTimeout = setTimeout(() => setPeerHeaderStatus("В сети"), 2000);
}

function clearPeerActivityStatus() {
    clearTimeout(peerActivityTimeout);
    setPeerHeaderStatus("В сети");
}

function markTransferProgress(bytes) {
    if (!currentFileTransfer) return;
    currentFileTransfer.lastProgressAt = Date.now();
    currentFileTransfer.lastProgressBytes = bytes;
}

function startTransferWatchdog(
    getBytes,
    onStall,
    intervalMs = FILE_STALL_CHECK_INTERVAL_MS,
) {
    let lastBytes = getBytes();
    let lastProgressAt = Date.now();
    return setInterval(() => {
        const currentBytes = getBytes();
        if (currentBytes > lastBytes) {
            lastBytes = currentBytes;
            lastProgressAt = Date.now();
            return;
        }
        if (Date.now() - lastProgressAt >= intervalMs * 2) {
            onStall();
        }
    }, intervalMs);
}

function cleanupCurrentTransferState() {
    removeFileTransferProgressElement();
    currentFileTransfer = null;
    planBOutgoingFileTransfer = null;
}

function waitForPlanBAck(targetSeq, timeoutMs = PLAN_B_ACK_TIMEOUT_MS) {
    return new Promise((resolve, reject) => {
        const startedAt = Date.now();
        const tick = () => {
            if (!planBOutgoingFileTransfer) {
                reject(new Error("Plan B transfer context missing"));
                return;
            }
            if (planBOutgoingFileTransfer.cancelled) {
                reject(new Error("Plan B transfer cancelled"));
                return;
            }
            if (planBOutgoingFileTransfer.lastAckedSeq >= targetSeq) {
                resolve();
                return;
            }
            if (Date.now() - startedAt > timeoutMs) {
                reject(new Error("Plan B ack timeout"));
                return;
            }
            setTimeout(tick, 40);
        };
        tick();
    });
}

function waitForBufferedAmountLow(channel, highWatermark) {
    if (!channel || channel.readyState !== "open") {
        return Promise.reject(new Error("Data channel is not open"));
    }
    if (channel.bufferedAmount <= highWatermark) {
        return Promise.resolve();
    }

    return new Promise((resolve, reject) => {
        const previousThreshold = channel.bufferedAmountLowThreshold;
        const cleanup = () => {
            channel.removeEventListener("bufferedamountlow", handleLow);
            channel.removeEventListener("close", handleClose);
            channel.removeEventListener("error", handleError);
            channel.bufferedAmountLowThreshold = previousThreshold;
        };
        const handleLow = () => {
            cleanup();
            resolve();
        };
        const handleClose = () => {
            cleanup();
            reject(new Error("Data channel closed while waiting for buffer"));
        };
        const handleError = () => {
            cleanup();
            reject(new Error("Data channel error while waiting for buffer"));
        };

        channel.bufferedAmountLowThreshold = highWatermark;
        channel.addEventListener("bufferedamountlow", handleLow, {
            once: true,
        });
        channel.addEventListener("close", handleClose, { once: true });
        channel.addEventListener("error", handleError, { once: true });
    });
}

function waitForBinaryTransferProgress(predicate, timeoutMs = 120000) {
    return new Promise((resolve, reject) => {
        const startedAt = Date.now();
        const tick = () => {
            if (!currentFileTransfer) {
                reject(new Error("Binary transfer context missing"));
                return;
            }
            if (currentFileTransfer.cancelled) {
                reject(new Error("Binary transfer cancelled"));
                return;
            }
            if (predicate(currentFileTransfer)) {
                resolve();
                return;
            }
            if (Date.now() - startedAt > timeoutMs) {
                reject(new Error("Binary transfer ack timeout"));
                return;
            }
            setTimeout(tick, 40);
        };
        tick();
    });
}

function sendActivitySignal(activity, active = true) {
    if (!peerId) return;
    void sendRealtimeSignal({ type: "activity", activity, active });
}

window.handleLocalTypingInput = function (text) {
    if (!peerId) return;
    if (text && text.trim()) {
        sendActivitySignal("typing", true);
        clearTimeout(typingStopTimeout);
        typingStopTimeout = setTimeout(() => {
            sendActivitySignal("typing", false);
        }, 1200);
        return;
    }
    sendActivitySignal("typing", false);
};

// ========== ЗАПУСК ЧАТА ==========
window.startChat = async function (data) {
    if (pc) {
        try {
            pc.onicecandidate = null;
            pc.ondatachannel = null;
            pc.close();
        } catch (e) {
            console.warn("[PC] Ошибка закрытия старого соединения:", e);
        }
    }
    peerId = data.peer_id;
    peerNickname = data.peer_nickname;
    window.currentChatPeer = { id: peerId, nickname: peerNickname };
    const role = data.role;
    window.receivingFile = null;
    pendingRemoteCandidates = [];
    resetConnectionReadiness();
    messages = [];
    flushReadReceipts();

    startConnectionTimers("Устанавливаем защищённое соединение...");
    setConnectionOverlayVisible(
        true,
        buildConnectionTimerText("Устанавливаем защищённое соединение..."),
    );
    updateChatStatus("Устанавливается защищённое соединение...");
    syncTransportState("chat_started");
    clearTimeout(planBTimer);
    planBTimer = setTimeout(() => {
        const ready =
            rtcState.iceConnected && rtcState.dataOpen && rtcState.keyReady;
        if (!ready) {
            activatePlanB();
        }
    }, PLAN_B_ACTIVATION_DELAY_MS);
    clearTimeout(connectionTimeout);
    connectionTimeout = setTimeout(() => {
        if (planBActive) return;
        if (
            pc &&
            pc.iceConnectionState !== "connected" &&
            pc.iceConnectionState !== "completed"
        ) {
            updateChatStatus(
                "Соединение устанавливается долго. Проверьте интернет или попробуйте перезайти.",
            );
            setConnectionOverlayVisible(
                true,
                "Соединение устанавливается долго. Ожидаем сеть...",
            );
            addSystemMessage(
                "Соединение устанавливается дольше обычного. Продолжаем попытки и при необходимости включим Plan B автоматически.",
            );
        }
    }, CONNECTION_WARNING_DELAY_MS);

    const iceServers = await getIceServers();

    pc = new RTCPeerConnection({
        iceServers,
        iceCandidatePoolSize: 10,
        iceTransportPolicy: "all",
        bundlePolicy: "max-bundle",
        rtcpMuxPolicy: "require",
        // iceTransportPolicy: forceTurn ? "relay" : "all",
    });

    pc.oniceconnectionstatechange = () => {
        console.log("[ICE] iceConnectionState:", pc.iceConnectionState);
        syncTransportState("ice_state_changed", false);
        switch (pc.iceConnectionState) {
            case "checking":
                updateChatStatus("Поиск пути для соединения...");
                break;
            case "connected":
            case "completed":
                rtcState.iceConnected = true;
                clearTimeout(connectionTimeout);
                trySendReady();
                evaluateConnectionReady();
                if (!sharedKey && role === "peer") {
                    ws.send(
                        JSON.stringify({
                            type: "public_key_request",
                            data: { to: peerId },
                        }),
                    );
                }
                break;
            case "failed":
                if (planBActive) {
                    console.warn(
                        "⚠️ ICE failed, но Plan B уже активен — продолжаем через сервер",
                    );
                    break;
                }
                rtcState.iceConnected = false;
                clearTimeout(connectionTimeout);

                connectionAttempts++;
                console.warn(`❌ ICE failed (попытка ${connectionAttempts})`);

                if (connectionAttempts < MAX_TURN_ATTEMPTS && forceTurn) {
                    const retryMode = usingTurn
                        ? "TURN + STUN"
                        : "STUN-only (TURN не подтверждён)";
                    console.log(
                        `🔄 Повторная попытка ICE (${retryMode}, попытка ${connectionAttempts + 1})...`,
                    );

                    setTimeout(() => {
                        window.startChat({
                            peer_id: peerId,
                            peer_nickname: peerNickname,
                            role: role,
                        });
                    }, 1000);

                    return;
                }

                console.warn("⚠️ P2P не установился → включаем Plan B");
                connectionAttempts = 0;

                activatePlanB();
                updateChatStatus("Включаем резервное соединение Plan B...");
                break;
            case "disconnected":
                if (planBActive) {
                    console.warn(
                        "⚠️ ICE disconnected, но Plan B активен — не считаем это потерей чата",
                    );
                    break;
                }
                rtcState.iceConnected = false;
                updateChatStatus("Соединение потеряно");
                setConnectionOverlayVisible(true, "Соединение потеряно...");
                break;
            case "closed":
                rtcState.iceConnected = false;
                break;
        }
    };
    pc.onconnectionstatechange = () => {
        console.log("[PC] connectionState:", pc.connectionState);
        if (planBActive && pc.connectionState === "failed") {
            console.warn(
                "⚠️ RTCPeerConnection failed, но Plan B активен — чат продолжает работать через WebSocket",
            );
            setConnectionOverlayVisible(false);
            updateChatStatus("Plan B: серверный защищённый канал активен");
        }
    };
    pc.onicegatheringstatechange = () =>
        console.log("[ICE] gatheringState:", pc.iceGatheringState);
    pc.onicecandidate = (e) => {
        if (e.candidate) {
            console.log(`[ICE] Кандидат (${e.candidate.type})`);
            ws.send(
                JSON.stringify({
                    type: "candidate",
                    data: { to: peerId, candidate: e.candidate },
                }),
            );
        } else {
            console.log("[ICE] Сбор кандидатов завершён");
        }
    };

    try {
        myKeyPair = await crypto.subtle.generateKey(
            { name: "ECDH", namedCurve: "P-256" },
            true,
            ["deriveKey"],
        );
        const pub = uint8ArrayToBase64(
            new Uint8Array(
                await crypto.subtle.exportKey("raw", myKeyPair.publicKey),
            ),
        );
        myPublicKeyBase64 = pub;
        ws.send(
            JSON.stringify({
                type: "public_key",
                data: { to: peerId, key: pub },
            }),
        );
        console.log("[KEY] Публичный ключ отправлен");
    } catch (err) {
        console.error("[KEY] Error generating keys:", err);
        updateChatStatus("Ошибка генерации ключей шифрования");
        return;
    }

    if (role === "host") {
        dataChannel = pc.createDataChannel("chat");
        setupDataChannel();
        fileChannel = pc.createDataChannel("fileTransfer", { ordered: true });
        setupFileChannel();
        try {
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            ws.send(
                JSON.stringify({
                    type: "offer",
                    data: { to: peerId, offer: pc.localDescription },
                }),
            );
            console.log("[SIGNAL] Offer отправлен");
        } catch (err) {
            console.error("[SIGNAL] Error creating offer:", err);
            updateChatStatus("Ошибка создания соединения");
        }
    } else {
        pc.ondatachannel = (e) => {
            console.log("[DC] DataChannel получен:", e.channel.label);
            if (e.channel.label === "chat") {
                dataChannel = e.channel;
                setupDataChannel();
            } else if (e.channel.label === "fileTransfer") {
                fileChannel = e.channel;
                setupFileChannel();
            }
        };
    }
};

function setupDataChannel() {
    dataChannel.onopen = () => {
        console.log("[DC] ✅ Основной канал открыт");
        clearTimeout(connectionTimeout);
        rtcState.dataOpen = true;
        trySendReady();
        evaluateConnectionReady();
        syncTransportState("data_channel_opened");
        if (!sharedKey)
            addSystemMessage(
                "Ключи шифрования ещё не согласованы. Ожидаем обмен ключами...",
            );
    };
    dataChannel.onclose = () => {
        console.log("[DC] Основной канал закрыт");
        rtcState.dataOpen = false;
        if (planBActive) {
            showPlanBReadyState();
            syncTransportState("data_channel_closed_during_plan_b", false);
            return;
        }
        addSystemMessage("Собеседник отключился");
        setConnectionOverlayVisible(true, "Собеседник отключился");
        syncTransportState("data_channel_closed");
    };
    dataChannel.onerror = (err) => {
        console.error("[DC] Ошибка:", err);
        updateChatStatus("Ошибка соединения");
    };

    dataChannel.onmessage = async (e) => {
        try {
            const data = JSON.parse(e.data);
            if (data.type === "file_metadata") {
                if (
                    data.binary &&
                    fileChannel &&
                    fileChannel.readyState === "open"
                ) {
                    console.log(
                        "[FILE] Бинарный режим получения файла:",
                        data.name,
                    );
                    receivingFiles[data.fileId] = {
                        fileId: data.fileId,
                        name: data.name,
                        size: data.size,
                        iv: new Uint8Array(data.iv),
                        mimeType: data.mimeType,
                        chunks: {},
                        received: 0,
                        isImage: data.isImage,
                        streamEncrypted: Boolean(data.streamEncrypted),
                    };
                    addSystemMessage(
                        `📥 Получение файла (бинарный): ${data.name}`,
                    );
                    updateChatStatus(`📥 0%`);
                } else {
                    console.log(
                        "[FILE] JSON-режим получения файла (fallback):",
                        data.name,
                    );
                    window.receivingFile = {
                        fileId: data.fileId,
                        name: data.name,
                        size: data.size,
                        iv: new Uint8Array(data.iv),
                        mimeType: data.mimeType,
                        chunks: {},
                        received: 0,
                        isImage: data.isImage,
                    };
                    addSystemMessage(`📥 Получение файла: ${data.name}`);
                    updateChatStatus(`📥 0%`);
                }
                return;
            }

            if (data.type === "ready") {
                remoteReadyReceived = true;
                evaluateConnectionReady();
                return;
            }

            if (data.type === "activity") {
                if (data.active) showPeerActivityStatus(data.activity);
                else clearPeerActivityStatus();
                return;
            }

            if (data.type === "read_receipt") {
                markMessageAsRead(data.messageId);
                return;
            }

            if (data.type === "file_progress_ack" && currentFileTransfer) {
                if (currentFileTransfer.fileId === data.fileId) {
                    currentFileTransfer.confirmedBytes = Math.max(
                        currentFileTransfer.confirmedBytes || 0,
                        data.received || 0,
                    );
                }
                return;
            }

            if (data.type === "file_received" && currentFileTransfer) {
                if (currentFileTransfer.fileId === data.fileId) {
                    currentFileTransfer.confirmedBytes = Math.max(
                        currentFileTransfer.confirmedBytes || 0,
                        data.received || 0,
                    );
                    currentFileTransfer.receivedComplete = true;
                }
                return;
            }

            if (
                data.type === "file_chunk" &&
                window.receivingFile &&
                !receivingFiles[data.fileId]
            ) {
                if (window.receivingFile.fileId === data.fileId) {
                    window.receivingFile.chunks[data.offset] = Uint8Array.from(
                        data.data,
                    );
                    window.receivingFile.received += data.data.length;
                    const progress = Math.round(
                        (window.receivingFile.received /
                            window.receivingFile.size) *
                            100,
                    );
                    updateChatStatus(`📥 ${progress}%`);
                }
                return;
            }

            if (
                data.type === "file_end" &&
                window.receivingFile &&
                !receivingFiles[data.fileId]
            ) {
                if (window.receivingFile.fileId === data.fileId) {
                    await finalizeJsonFileReceive(window.receivingFile);
                    dataChannel.send(
                        JSON.stringify({
                            type: "file_received",
                            fileId: data.fileId,
                            received: window.receivingFile.size,
                        }),
                    );
                    window.receivingFile = null;
                    updateChatStatus("");
                    addSystemMessage("✅ Файл получен");
                }
                return;
            }

            if (data.type === "file_cancel") {
                // Отмена у получателя (для обоих режимов)
                if (
                    window.receivingFile &&
                    window.receivingFile.fileId === data.fileId
                ) {
                    window.receivingFile = null;
                    updateChatStatus("");
                    addSystemMessage("📤 Отправка файла отменена");
                }
                if (receivingFiles[data.fileId]) {
                    delete receivingFiles[data.fileId];
                    updateChatStatus("");
                    addSystemMessage("📤 Отправка файла отменена");
                }
                return;
            }

            if (data.type === "message") {
                if (!sharedKey) {
                    console.warn(
                        "[MSG] Получено сообщение до согласования ключей — игнорируем",
                    );
                    return;
                }
                const iv = new Uint8Array(data.iv);
                const encrypted = base64ToUint8Array(data.encrypted);
                const decrypted = await crypto.subtle.decrypt(
                    { name: "AES-GCM", iv },
                    sharedKey,
                    encrypted,
                );
                const text = new TextDecoder().decode(decrypted);
                const messageId = data.messageId || crypto.randomUUID();
                messages.push({
                    id: messageId,
                    from: "other",
                    text: text,
                    time: new Date().toLocaleTimeString("ru-RU", {
                        hour: "2-digit",
                        minute: "2-digit",
                    }),
                    replyTo: data.replyTo,
                    senderName: peerNickname,
                });
                renderMessages();
                queueReadReceipt(messageId);
            } else if (data.type === "typing") {
                showTypingIndicator();
            }
        } catch (err) {
            console.error("[MSG] Error parsing message:", err);
        }
    };
}

function setupFileChannel() {
    if (!fileChannel) return;
    fileChannel.binaryType = "arraybuffer";
    fileChannel.onopen = () => {
        console.log("[FILE] ✅ Файловый канал открыт (бинарный режим)");
        setTimeout(() => {
            if (fileChannel && fileChannel.readyState === "open") {
                fileChannelReady = true;
                binaryFileSupported = true;
                console.log("[FILE] Файловый канал готов к отправке");
            }
        }, 200);
    };
    fileChannel.onclose = () => {
        console.log("[FILE] Файловый канал закрыт");
        fileChannelReady = false;
        binaryFileSupported = false;
    };
    fileChannel.onerror = (err) => {
        console.error("[FILE] Ошибка файлового канала:", err);
        fileChannelReady = false;
        binaryFileSupported = false;
        addSystemMessage(
            "⚠️ Быстрый режим передачи файлов недоступен, используется стандартный режим.",
        );
    };

    fileChannel.onmessage = async (event) => {
        if (!(event.data instanceof ArrayBuffer)) return;
        if (event.data.byteLength < 12) return;
        const view = new DataView(event.data);
        const fileId = view.getUint32(0);
        const offset = Number(view.getBigUint64(4));
        const chunkData = event.data.slice(12);

        const receiving = receivingFiles[fileId];
        if (!receiving) {
            console.warn(
                `[FILE] Получен чанк для неизвестного fileId ${fileId}`,
            );
            return;
        }

        let payloadChunk;
        if (receiving.streamEncrypted) {
            const encryptedBytes = new Uint8Array(chunkData);
            const chunkIv = encryptedBytes.slice(0, 12);
            const encryptedChunk = encryptedBytes.slice(12);
            const decryptedChunk = await crypto.subtle.decrypt(
                { name: "AES-GCM", iv: chunkIv },
                sharedKey,
                encryptedChunk,
            );
            payloadChunk = new Uint8Array(decryptedChunk);
        } else {
            payloadChunk = new Uint8Array(chunkData);
        }

        receiving.chunks[offset] = payloadChunk;
        receiving.received += payloadChunk.byteLength;
        receiving.receivedChunks = (receiving.receivedChunks || 0) + 1;
        const progress = Math.round(
            (receiving.received / receiving.size) * 100,
        );
        updateChatStatus(`📥 ${progress}%`);
        if (
            dataChannel &&
            dataChannel.readyState === "open" &&
            (receiving.receivedChunks % BINARY_ACK_EVERY_CHUNKS === 0 ||
                receiving.received === receiving.size)
        ) {
            dataChannel.send(
                JSON.stringify({
                    type: "file_progress_ack",
                    fileId,
                    received: receiving.received,
                }),
            );
        }

        if (receiving.received === receiving.size) {
            await finalizeBinaryFileReceive(receiving);
            if (dataChannel && dataChannel.readyState === "open") {
                dataChannel.send(
                    JSON.stringify({
                        type: "file_received",
                        fileId,
                        received: receiving.received,
                    }),
                );
            }
            delete receivingFiles[fileId];
            updateChatStatus("");
            addSystemMessage("✅ Файл получен");
        }
    };
}

async function finalizeJsonFileReceive(receiving) {
    const chunks = Object.entries(receiving.chunks)
        .sort(([a], [b]) => parseInt(a) - parseInt(b))
        .map(([, chunk]) => chunk);
    const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const merged = new Uint8Array(totalLength);
    let offset = 0;
    for (const chunk of chunks) {
        merged.set(chunk, offset);
        offset += chunk.length;
    }
    let fileBytes = merged;
    if (!receiving.streamEncrypted) {
        const decrypted = await crypto.subtle.decrypt(
            { name: "AES-GCM", iv: receiving.iv },
            sharedKey,
            merged,
        );
        fileBytes = decrypted;
    }
    const blob = new Blob([fileBytes], { type: receiving.mimeType });
    const url = URL.createObjectURL(blob);
    const messageType = receiving.isImage ? "image" : "file";
    messages.push({
        from: "other",
        text: receiving.name,
        size: receiving.size,
        type: messageType,
        url: url,
        isImage: receiving.isImage,
        time: new Date().toLocaleTimeString("ru-RU", {
            hour: "2-digit",
            minute: "2-digit",
        }),
    });
    renderMessages();
}

async function finalizeBinaryFileReceive(receiving) {
    const chunks = Object.entries(receiving.chunks)
        .sort(([a], [b]) => parseInt(a) - parseInt(b))
        .map(([, chunk]) => chunk);
    const totalLength = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const merged = new Uint8Array(totalLength);
    let offset = 0;
    for (const chunk of chunks) {
        merged.set(chunk, offset);
        offset += chunk.length;
    }
    let fileBytes = merged;
    if (!receiving.streamEncrypted) {
        const decrypted = await crypto.subtle.decrypt(
            { name: "AES-GCM", iv: receiving.iv },
            sharedKey,
            merged,
        );
        fileBytes = decrypted;
    }
    const blob = new Blob([fileBytes], { type: receiving.mimeType });
    const url = URL.createObjectURL(blob);
    const messageType = receiving.isImage ? "image" : "file";
    messages.push({
        from: "other",
        text: receiving.name,
        size: receiving.size,
        type: messageType,
        url: url,
        isImage: receiving.isImage,
        time: new Date().toLocaleTimeString("ru-RU", {
            hour: "2-digit",
            minute: "2-digit",
        }),
    });
    renderMessages();
}

async function sendFile(file) {
    if (currentFileTransfer) {
        updateChatStatus(
            "Дождитесь завершения текущей передачи или отмените её",
        );
        return;
    }
    if (!canSendEncryptedPayload()) {
        return;
    }
    if (file.size > MAX_TRANSFER_FILE_SIZE) {
        const limit = formatFileSize(MAX_TRANSFER_FILE_SIZE);
        updateChatStatus(`Файл превышает лимит ${limit}`);
        addSystemMessage(`⚠️ Максимальный размер файла: ${limit}.`);
        return;
    }
    if (!planBActive && (!dataChannel || dataChannel.readyState !== "open")) {
        updateChatStatus("Соединение ещё не установлено");
        return;
    }
    sendActivitySignal(
        isImageFile(file.type) ? "sending_image" : "sending_file",
        true,
    );
    try {
        if (planBActive) {
            await sendFilePlanB(file);
            return;
        }

        if (
            binaryFileSupported &&
            fileChannel &&
            fileChannel.readyState === "open" &&
            fileChannelReady
        ) {
            try {
                await sendFileBinary(file);
            } catch (err) {
                if (isTransferCancellationError(err)) {
                    console.log(
                        "[FILE] Бинарная отправка отменена пользователем",
                    );
                    return;
                }
                console.error(
                    "[FILE] Ошибка при бинарной отправке, переключаемся на JSON:",
                    err,
                );
                binaryFileSupported = false;
                await sendFileJson(file);
            }
        } else {
            console.log(
                "[FILE] Бинарный канал недоступен, используем JSON-режим (fallback)",
            );
            await sendFileJson(file);
        }
    } catch (err) {
        if (isTransferCancellationError(err)) {
            console.log("[FILE] Отправка файла отменена пользователем");
            return;
        }
        console.error("[FILE] Ошибка отправки файла:", err);
        if (
            !planBActive &&
            ws &&
            ws.readyState === WebSocket.OPEN &&
            sharedKey
        ) {
            addSystemMessage(
                "⚠️ P2P-передача остановилась. Переключаемся на защищённый серверный канал и отправляем файл заново.",
            );
            cleanupCurrentTransferState();
            activatePlanB();
            await sendFilePlanB(file);
            return;
        }
        addSystemMessage("❌ Ошибка отправки файла. Попробуйте ещё раз.");
        updateChatStatus("Ошибка отправки файла");
    } finally {
        sendActivitySignal("sending_file", false);
        sendActivitySignal("sending_image", false);
    }
}

async function sendFileBinary(file) {
    const fileId = nextFileId++;
    const isImage = isImageFile(file.type);

    dataChannel.send(
        JSON.stringify({
            type: "file_metadata",
            binary: true,
            streamEncrypted: true,
            fileId: fileId,
            name: file.name,
            size: file.size,
            iv: [],
            mimeType: file.type || "application/octet-stream",
            isImage: isImage,
        }),
    );

    let cancelled = false;
    const cancelHandler = () => {
        if (cancelled) return;
        cancelled = true;
        if (currentFileTransfer) currentFileTransfer.cancelled = true;
        if (dataChannel && dataChannel.readyState === "open") {
            dataChannel.send(
                JSON.stringify({ type: "file_cancel", fileId: fileId }),
            );
        }
        removeFileTransferProgressElement();
        updateChatStatus("передача файла отменена");
    };
    createFileTransferProgressElement(cancelHandler);
    currentFileTransfer = {
        fileId,
        cancel: cancelHandler,
        cancelled: false,
        confirmedBytes: 0,
        receivedComplete: false,
    };

    let offset = 0;
    const startedAt = Date.now();
    let watchdogTriggered = false;
    const watchdog = startTransferWatchdog(
        () => currentFileTransfer?.confirmedBytes || 0,
        () => {
            watchdogTriggered = true;
            if (fileChannel) fileChannel.close();
        },
    );

    try {
        while (offset < file.size && !cancelled) {
            await waitForBufferedAmountLow(
                fileChannel,
                FILE_CHANNEL_BUFFER_HIGH_WATERMARK,
            );
            if (cancelled) break;

            while (
                currentFileTransfer &&
                offset - currentFileTransfer.confirmedBytes >
                    BINARY_MAX_UNACKED_BYTES &&
                !cancelled
            ) {
                await waitForBinaryTransferProgress(
                    (transfer) =>
                        offset - transfer.confirmedBytes <=
                        BINARY_MAX_UNACKED_BYTES,
                );
            }
            if (cancelled) break;

            const plainChunk = new Uint8Array(
                await file
                    .slice(offset, offset + BINARY_FILE_CHUNK_SIZE)
                    .arrayBuffer(),
            );
            const chunkIv = crypto.getRandomValues(new Uint8Array(12));
            const encryptedChunk = new Uint8Array(
                await crypto.subtle.encrypt(
                    { name: "AES-GCM", iv: chunkIv },
                    sharedKey,
                    plainChunk,
                ),
            );
            const header = new ArrayBuffer(12);
            const headerView = new DataView(header);
            headerView.setUint32(0, fileId);
            headerView.setBigUint64(4, BigInt(offset));
            const combined = new Uint8Array(
                header.byteLength +
                    chunkIv.byteLength +
                    encryptedChunk.byteLength,
            );
            combined.set(new Uint8Array(header), 0);
            combined.set(chunkIv, header.byteLength);
            combined.set(
                encryptedChunk,
                header.byteLength + chunkIv.byteLength,
            );
            fileChannel.send(combined.buffer);

            offset += plainChunk.length;
            const queuedProgress = Math.round((offset / file.size) * 100);
            const confirmed = currentFileTransfer?.confirmedBytes || 0;
            const confirmedProgress = Math.round((confirmed / file.size) * 100);
            updateFileTransferProgress(
                queuedProgress,
                `${formatFileSize(confirmed)} получено / ${formatFileSize(offset)} отправлено из ${formatFileSize(file.size)} • ${getTransferSpeedLabel(startedAt, confirmed || offset)}`,
            );
            updateChatStatus(`📤 ${confirmedProgress}% подтверждено`);
        }

        if (cancelled) {
            // Добавляем системное сообщение об отмене для отправителя
            messages.push({
                system: true,
                text: "📤 Отправка файла отменена",
                time: new Date().toLocaleTimeString("ru-RU", {
                    hour: "2-digit",
                    minute: "2-digit",
                }),
            });
            renderMessages();
        } else {
            dataChannel.send(
                JSON.stringify({ type: "file_end", fileId: fileId }),
            );
            updateChatStatus(
                "📤 100% отправлено. Ждём подтверждение получения...",
            );
            await waitForBinaryTransferProgress(
                (transfer) => transfer.receivedComplete,
                10 * 60 * 1000,
            );
            const url = URL.createObjectURL(file);
            const messageType = isImage ? "image" : "file";
            messages.push({
                from: "me",
                text: file.name,
                size: file.size,
                type: messageType,
                url: url,
                isImage: isImage,
                time: new Date().toLocaleTimeString("ru-RU", {
                    hour: "2-digit",
                    minute: "2-digit",
                }),
            });
            renderMessages();
        }
    } finally {
        clearInterval(watchdog);
    }
    if (watchdogTriggered) throw new Error("Binary transfer stalled");

    removeFileTransferProgressElement();
    currentFileTransfer = null;
    updateChatStatus("");
}

async function sendFileJson(file) {
    const fileId = crypto.randomUUID();

    let cancelled = false;
    const cancelHandler = () => {
        if (cancelled) return;
        cancelled = true;
        if (currentFileTransfer) currentFileTransfer.cancelled = true;
        if (dataChannel && dataChannel.readyState === "open") {
            dataChannel.send(
                JSON.stringify({ type: "file_cancel", fileId: fileId }),
            );
        }
        removeFileTransferProgressElement();
        updateChatStatus("передача файла отменена");
    };
    createFileTransferProgressElement(cancelHandler);
    currentFileTransfer = { cancel: cancelHandler, cancelled: false };

    const iv = crypto.getRandomValues(new Uint8Array(12));
    const fileData = await file.arrayBuffer();
    if (cancelled) {
        currentFileTransfer = null;
        return;
    }
    const encrypted = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv },
        sharedKey,
        fileData,
    );
    if (cancelled) {
        currentFileTransfer = null;
        return;
    }
    const isImage = isImageFile(file.type);

    dataChannel.send(
        JSON.stringify({
            type: "file_metadata",
            fileId,
            name: file.name,
            size: encrypted.byteLength,
            iv: Array.from(iv),
            mimeType: file.type || "application/octet-stream",
            isImage: isImage,
        }),
    );

    const CHUNK = 64 * 1024;
    const data = new Uint8Array(encrypted);
    let offset = 0;
    const startedAt = Date.now();
    let watchdogTriggered = false;
    const watchdog = startTransferWatchdog(
        () => offset,
        () => {
            watchdogTriggered = true;
        },
    );

    try {
        while (offset < data.length && !cancelled) {
            if (
                !dataChannel ||
                dataChannel.readyState !== "open" ||
                watchdogTriggered
            ) {
                throw new Error("JSON transfer stalled or data channel closed");
            }
            while (dataChannel.bufferedAmount > 1_000_000 && !cancelled) {
                await new Promise((r) => setTimeout(r, 10));
            }
            if (cancelled) break;
            const chunk = data.slice(offset, offset + CHUNK);
            dataChannel.send(
                JSON.stringify({
                    type: "file_chunk",
                    fileId,
                    offset,
                    total: data.length,
                    data: Array.from(chunk),
                }),
            );
            offset += CHUNK;
            const progress = Math.round((offset / data.length) * 100);
            updateFileTransferProgress(
                progress,
                `${(offset / 1024 / 1024).toFixed(1)} MB / ${(data.length / 1024 / 1024).toFixed(1)} MB • ${getTransferSpeedLabel(startedAt, offset)}`,
            );
            updateChatStatus(`📤 ${progress}%`);
        }

        if (cancelled) {
            // Добавляем системное сообщение об отмене для отправителя
            messages.push({
                system: true,
                text: "📤 Отправка файла отменена",
                time: new Date().toLocaleTimeString("ru-RU", {
                    hour: "2-digit",
                    minute: "2-digit",
                }),
            });
            renderMessages();
        } else {
            dataChannel.send(JSON.stringify({ type: "file_end", fileId }));
            const originalBlob = new Blob([fileData], { type: file.type });
            const url = URL.createObjectURL(originalBlob);
            const messageType = isImage ? "image" : "file";
            messages.push({
                from: "me",
                text: file.name,
                size: file.size,
                type: messageType,
                url: url,
                isImage: isImage,
                time: new Date().toLocaleTimeString("ru-RU", {
                    hour: "2-digit",
                    minute: "2-digit",
                }),
            });
            renderMessages();
        }
    } finally {
        clearInterval(watchdog);
    }

    removeFileTransferProgressElement();
    currentFileTransfer = null;
    updateChatStatus("");
}

async function sendFilePlanB(file) {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        updateChatStatus("Plan B недоступен: нет подключения к серверу");
        return;
    }

    if (file.size > PLAN_B_MAX_FILE_SIZE) {
        updateChatStatus(
            `Plan B ограничен ${formatFileSize(PLAN_B_MAX_FILE_SIZE)}. Используйте P2P для больших файлов.`,
        );
        addSystemMessage(
            `⚠️ Файл слишком большой для Plan B (${formatFileSize(file.size)}). Лимит: ${formatFileSize(PLAN_B_MAX_FILE_SIZE)}.`,
        );
        return;
    }

    const fileId = crypto.randomUUID();
    let cancelled = false;
    planBOutgoingFileTransfer = {
        fileId,
        lastAckedSeq: -1,
        sentSeq: -1,
        cancelled: false,
    };
    const cancelHandler = () => {
        if (cancelled) return;
        cancelled = true;
        if (planBOutgoingFileTransfer) {
            planBOutgoingFileTransfer.cancelled = true;
        }
        if (currentFileTransfer) currentFileTransfer.cancelled = true;
        void sendRealtimeSignal({ type: "file_cancel", fileId });
        removeFileTransferProgressElement();
        updateChatStatus("передача файла отменена");
    };
    createFileTransferProgressElement(cancelHandler);
    currentFileTransfer = { cancel: cancelHandler, cancelled: false };

    try {
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const fileData = await file.arrayBuffer();
        if (cancelled) return;
        const encrypted = await crypto.subtle.encrypt(
            { name: "AES-GCM", iv },
            sharedKey,
            fileData,
        );
        if (cancelled) return;
        const isImage = isImageFile(file.type);
        const data = new Uint8Array(encrypted);

        await sendRealtimeSignal({
            type: "file_metadata",
            fileId,
            name: file.name,
            size: data.length,
            iv: Array.from(iv),
            mimeType: file.type || "application/octet-stream",
            isImage,
            transport: "plan_b",
        });
        let offset = 0;
        let seq = 0;
        const startedAt = Date.now();
        let watchdogTriggered = false;
        const watchdog = startTransferWatchdog(
            () => planBOutgoingFileTransfer?.lastAckedSeq || -1,
            () => {
                watchdogTriggered = true;
            },
            FILE_STALL_CHECK_INTERVAL_MS,
        );
        try {
            while (offset < data.length && !cancelled) {
                while (
                    !cancelled &&
                    planBOutgoingFileTransfer &&
                    planBOutgoingFileTransfer.sentSeq -
                        planBOutgoingFileTransfer.lastAckedSeq >=
                        PLAN_B_MAX_IN_FLIGHT
                ) {
                    await waitForPlanBAck(
                        planBOutgoingFileTransfer.lastAckedSeq + 1,
                    );
                }
                const chunk = data.slice(
                    offset,
                    offset + PLAN_B_FILE_CHUNK_SIZE,
                );
                const chunkBase64 = uint8ArrayToBase64(chunk);
                await sendRealtimeSignal({
                    type: "file_chunk",
                    fileId,
                    seq,
                    offset,
                    data: chunkBase64,
                });
                if (planBOutgoingFileTransfer) {
                    planBOutgoingFileTransfer.sentSeq = seq;
                }
                offset += chunk.length;
                seq += 1;
                const progress = Math.round((offset / data.length) * 100);
                updateFileTransferProgress(
                    progress,
                    `${(offset / 1024 / 1024).toFixed(1)} MB / ${(data.length / 1024 / 1024).toFixed(1)} MB • ${getTransferSpeedLabel(startedAt, offset)}`,
                );
                updateChatStatus(`📤 ${progress}%`);

                await new Promise((resolve) => setTimeout(resolve, 0));
            }

            if (!cancelled && planBOutgoingFileTransfer?.sentSeq >= 0) {
                await waitForPlanBAck(
                    planBOutgoingFileTransfer.sentSeq,
                    PLAN_B_ACK_TIMEOUT_MS * 2,
                );
            }
            if (watchdogTriggered) {
                throw new Error("Plan B transfer stalled");
            }

            if (cancelled) {
                messages.push({
                    system: true,
                    text: "📤 Отправка файла отменена",
                    time: new Date().toLocaleTimeString("ru-RU", {
                        hour: "2-digit",
                        minute: "2-digit",
                    }),
                });
                renderMessages();
            } else {
                await sendRealtimeSignal({
                    type: "file_end",
                    fileId,
                });
                const originalBlob = new Blob([fileData], { type: file.type });
                const url = URL.createObjectURL(originalBlob);
                const messageType = isImage ? "image" : "file";
                messages.push({
                    from: "me",
                    text: file.name,
                    size: file.size,
                    type: messageType,
                    url,
                    isImage,
                    time: new Date().toLocaleTimeString("ru-RU", {
                        hour: "2-digit",
                        minute: "2-digit",
                    }),
                });
                renderMessages();
            }
        } finally {
            clearInterval(watchdog);
        }
    } finally {
        removeFileTransferProgressElement();
        currentFileTransfer = null;
        planBOutgoingFileTransfer = null;
        updateChatStatus("");
    }
}

// ========== ОСТАЛЬНЫЕ ФУНКЦИИ (отправка сообщений, рендер, статус) ==========
async function sendMessage() {
    const input = document.getElementById("messageInput");
    const text = input.value.trim();
    if (!text) return;

    const quote = document.querySelector(".message-quote");
    let replyToId = quote ? quote.dataset.replyToId : null;

    if (!canSendEncryptedPayload()) {
        return;
    }

    try {
        const messageId = crypto.randomUUID();
        if (planBActive) {
            if (!ws || ws.readyState !== WebSocket.OPEN) {
                updateChatStatus(
                    "Plan B недоступен: нет подключения к серверу сигналинга",
                );
                return;
            }

            await sendRealtimeSignal({
                type: "message",
                messageId,
                text,
                replyTo: replyToId,
            });
        } else {
            if (!dataChannel || dataChannel.readyState !== "open") {
                updateChatStatus("Соединение ещё не установлено");
                return;
            }
            const iv = crypto.getRandomValues(new Uint8Array(12));
            const encoded = new TextEncoder().encode(text);
            const encrypted = await crypto.subtle.encrypt(
                { name: "AES-GCM", iv },
                sharedKey,
                encoded,
            );
            const messagePayload = {
                type: "message",
                messageId,
                iv: Array.from(iv),
                encrypted: uint8ArrayToBase64(new Uint8Array(encrypted)),
                replyTo: replyToId,
            };
            dataChannel.send(JSON.stringify(messagePayload));
        }

        messages.push({
            id: messageId,
            from: "me",
            text: text,
            read: false,
            time: new Date().toLocaleTimeString("ru-RU", {
                hour: "2-digit",
                minute: "2-digit",
            }),
            replyTo: replyToId,
            senderName: "Вы",
        });
        renderMessages();
        input.value = "";
        window.handleLocalTypingInput("");
        if (quote) quote.remove();
    } catch (err) {
        console.error("[MSG] Error sending message:", err);
        updateChatStatus("Ошибка отправки сообщения");
    }
}

function renderMessages() {
    const container = document.getElementById("messages");
    if (!container) return;
    container.innerHTML = messages
        .map((m) => {
            if (m.system)
                return `<div class="system-message">${escapeHtml(m.text)}</div>`;
            if (m.type === "image") {
                return `
                <div class="message ${m.from} image" data-message-id="${m.id || ""}" data-image-url="${m.url || ""}" data-original-filename="${escapeAttr(m.text)}">
                    ${m.url ? `<img src="${m.url}" alt="${escapeHtml(m.text)}" class="chat-image" />` : ""}
                    <div>
                        <div class="file-name">${escapeHtml(m.text)}</div>
                        <div class="file-size">${formatFileSize(m.size)}</div>
                        <span class="message-time">${m.time}</span>
                    </div>
                </div>
            `;
            }
            if (m.type === "file") {
                return `
                <div class="message ${m.from} file" 
                    data-message-id="${m.id}" 
                    data-url="${m.url || ""}" 
                    data-original-filename="${escapeAttr(m.text)}"
                    style="display: flex; align-items: stretch; gap: 6px;">
                    
                    <!-- Иконка слева, растягивается на всю высоту -->
                    <div class="file-icon"> 
                        <svg width="24px" height="24px" viewBox="0 0 20 20" xmlns="http://www.w3.org/2000/svg" fill="none">
                            <path fill="#ffffff" fill-rule="evenodd" d="M4 1a2 2 0 00-2 2v14a2 2 0 002 2h12a2 2 0 002-2V7.414A2 2 0 0017.414 6L13 1.586A2 2 0 0011.586 1H4zm6.5 2H4v14h12V9.5h-3.5a2 2 0 01-2-2V3zM16 7.5h-3.5V3.914l3.5 3.5V7.5z"/>
                        </svg> 
                    </div>
                    
                    <!-- Правый блок: имя и размер сверху, время снизу справа -->
                    <div>
                        <div>
                            <div class="file-name">
                                ${
                                    m.url && !m.isImage
                                        ? `<a href="#" class="file-download-link" style="color: #3b82f6; text-decoration: none;" data-filename="${escapeAttr(m.text)}" data-url="${m.url}">${escapeHtml(m.text)}</a>`
                                        : escapeHtml(m.text)
                                }
                            </div>
                            <div class="file-size">${formatFileSize(m.size)}</div>
                        </div>
                        <div class="message-time" style="flex-direction: row-reverse;">${m.time}</div>
                    </div>
                </div>
            `;
            }
            let quoteHtml = "";
            if (m.replyTo) {
                const replyToMsg = messages.find((msg) => msg.id === m.replyTo);
                if (replyToMsg) {
                    const author =
                        replyToMsg.from === "me"
                            ? "Вы"
                            : replyToMsg.senderName ||
                              peerNickname ||
                              "Собеседник";
                    const text =
                        escapeHtml(replyToMsg.text.substring(0, 100)) +
                        (replyToMsg.text.length > 100 ? "..." : "");
                    quoteHtml = `<div class="message-quote"><div class="quote-author">${author}</div><div class="quote-text">${text}</div></div>`;
                }
            }
            const parsedText = window.parseLinks
                ? window.parseLinks(m.text)
                : escapeHtml(m.text);
            const readMark =
                m.from === "me"
                    ? `<span class="message-checks ${m.read ? "read" : "delivered"}">✓✓</span>`
                    : "";
            return `${quoteHtml}<div class="message ${m.from}" data-message-id="${m.id}"><span class="message-content">${parsedText}</span><span class="message-time">${m.time} ${readMark}</span></div>`;
        })
        .join("");
    container.scrollTop = container.scrollHeight;
}

function formatFileSize(bytes) {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB", "TB"];
    const i = Math.min(
        Math.floor(Math.log(bytes) / Math.log(k)),
        sizes.length - 1,
    );
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + " " + sizes[i];
}

function addSystemMessage(text) {
    messages.push({
        system: true,
        text: text,
        time: new Date().toLocaleTimeString("ru-RU", {
            hour: "2-digit",
            minute: "2-digit",
        }),
    });
    renderMessages();
}

function updateChatStatus(text) {
    setPeerHeaderStatus(text || "В сети");
}

let typingTimeout;
function showTypingIndicator() {
    if (peerNickname) {
        setPeerHeaderStatus(`${peerNickname} печатает...`);
        clearTimeout(typingTimeout);
        typingTimeout = setTimeout(() => setPeerHeaderStatus("В сети"), 2000);
    }
}

window.handleWebRTCMessage = async function (msg) {
    if (msg.type === "public_key_request" && myKeyPair) {
        const pub = uint8ArrayToBase64(
            new Uint8Array(
                await crypto.subtle.exportKey("raw", myKeyPair.publicKey),
            ),
        );
        myPublicKeyBase64 = pub;
        ws.send(
            JSON.stringify({
                type: "public_key",
                data: { to: msg.data.from, key: pub },
            }),
        );
        return;
    } else if (msg.type === "offer") {
        try {
            if (!pc) return;
            await pc.setRemoteDescription(msg.data.offer);
            while (pendingRemoteCandidates.length) {
                const candidate = pendingRemoteCandidates.shift();
                await pc.addIceCandidate(candidate);
            }
            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);
            ws.send(
                JSON.stringify({
                    type: "answer",
                    data: { to: msg.data.from, answer: pc.localDescription },
                }),
            );
        } catch (err) {
            console.error("[SIGNAL] Error handling offer:", err);
        }
    } else if (msg.type === "answer") {
        try {
            if (pc) {
                await pc.setRemoteDescription(msg.data.answer);
                while (pendingRemoteCandidates.length) {
                    const candidate = pendingRemoteCandidates.shift();
                    await pc.addIceCandidate(candidate);
                }
            }
        } catch (err) {
            console.error("[SIGNAL] Error handling answer:", err);
        }
    } else if (msg.type === "candidate") {
        try {
            if (pc && msg.data.candidate) {
                if (pc.remoteDescription && pc.remoteDescription.type) {
                    await pc.addIceCandidate(msg.data.candidate);
                } else {
                    pendingRemoteCandidates.push(msg.data.candidate);
                    console.log(
                        "[ICE] Кандидат поставлен в очередь до remoteDescription",
                    );
                }
            }
        } catch (err) {
            console.error("[ICE] Error adding candidate:", err);
        }
    } else if (msg.type === "relay_message") {
        try {
            activatePlanBFromRemote();
            const envelope = msg.data.payload;
            if (!envelope || envelope.mode !== "encrypted_payload") {
                console.warn("[PLAN-B] Отклонён нешифрованный relay payload");
                return;
            }
            const payload = await decryptPlanBPayload(envelope);
            if (!payload) return;
            if (payload.type === "file_metadata") {
                planBReceivingFiles[payload.fileId] = {
                    fileId: payload.fileId,
                    name: payload.name,
                    size: payload.size,
                    iv: new Uint8Array(payload.iv),
                    mimeType: payload.mimeType,
                    chunks: {},
                    received: 0,
                    receivedChunks: 0,
                    isImage: payload.isImage,
                };
                addSystemMessage(
                    `📥 Получение файла через Plan B: ${payload.name}`,
                );
                updateChatStatus("📥 0%");
                return;
            }

            if (payload.type === "file_chunk") {
                const receiving = planBReceivingFiles[payload.fileId];
                if (!receiving || !payload.data) return;
                const chunk = base64ToUint8Array(payload.data);
                receiving.chunks[payload.offset] = chunk;
                receiving.received += chunk.length;
                receiving.receivedChunks = (receiving.receivedChunks || 0) + 1;
                const progress = Math.round(
                    (receiving.received / receiving.size) * 100,
                );
                updateChatStatus(`📥 ${progress}%`);
                const seq =
                    typeof payload.seq === "number"
                        ? payload.seq
                        : receiving.receivedChunks - 1;
                if (
                    receiving.receivedChunks % PLAN_B_ACK_EVERY_CHUNKS === 0 ||
                    receiving.received === receiving.size
                ) {
                    await sendRealtimeSignal({
                        type: "file_ack",
                        fileId: payload.fileId,
                        ackSeq: seq,
                    });
                }
                return;
            }

            if (payload.type === "file_end") {
                const receiving = planBReceivingFiles[payload.fileId];
                if (!receiving) return;
                await finalizeJsonFileReceive(receiving);
                delete planBReceivingFiles[payload.fileId];
                updateChatStatus("");
                addSystemMessage("✅ Файл получен");
                return;
            }

            if (payload.type === "file_cancel") {
                delete planBReceivingFiles[payload.fileId];
                if (
                    planBOutgoingFileTransfer &&
                    planBOutgoingFileTransfer.fileId === payload.fileId
                ) {
                    planBOutgoingFileTransfer.cancelled = true;
                }
                updateChatStatus("");
                addSystemMessage("📤 Отправка файла отменена");
                return;
            }
            if (payload.type === "file_ack") {
                if (
                    planBOutgoingFileTransfer &&
                    planBOutgoingFileTransfer.fileId === payload.fileId &&
                    typeof payload.ackSeq === "number"
                ) {
                    planBOutgoingFileTransfer.lastAckedSeq = Math.max(
                        planBOutgoingFileTransfer.lastAckedSeq,
                        payload.ackSeq,
                    );
                }
                return;
            }
            if (payload.type === "activity") {
                if (payload.active) showPeerActivityStatus(payload.activity);
                else clearPeerActivityStatus();
                return;
            }
            if (payload.type === "read_receipt") {
                markMessageAsRead(payload.messageId);
                return;
            }
            let text = "";

            if (payload.type === "message") {
                text = payload.text || "";
            } else {
                console.warn(
                    "[PLAN-B] Неизвестный тип relay_message:",
                    payload.type,
                );
                return;
            }

            const messageId = payload.messageId || crypto.randomUUID();
            messages.push({
                id: messageId,
                from: "other",
                text: text,
                time: new Date().toLocaleTimeString("ru-RU", {
                    hour: "2-digit",
                    minute: "2-digit",
                }),
                replyTo: payload.replyTo,
                senderName: peerNickname,
            });
            renderMessages();
            queueReadReceipt(messageId);
        } catch (err) {
            console.error("[PLAN-B] Ошибка relay_message:", err);
        }
    } else if (msg.type === "public_key") {
        try {
            if (!myKeyPair) return;
            peerPublicKeyBase64 = msg.data.key;
            const theirPub = await crypto.subtle.importKey(
                "raw",
                base64ToUint8Array(msg.data.key),
                { name: "ECDH", namedCurve: "P-256" },
                true,
                [],
            );
            sharedKey = await crypto.subtle.deriveKey(
                { name: "ECDH", public: theirPub },
                myKeyPair.privateKey,
                { name: "AES-GCM", length: 256 },
                true,
                ["encrypt", "decrypt"],
            );
            console.log("[KEY] ✅ Ключи шифрования согласованы");
            if (keyExchangeStartedAt) {
                console.log(
                    `[KEY] Обмен ключами занял ${formatElapsedTime(Date.now() - keyExchangeStartedAt)}`,
                );
            }
            rtcState.keyReady = true;
            trySendReady();
            evaluateConnectionReady();
            addSystemMessage("Ключи шифрования согласованы");
            safetyNumber = await computeSafetyNumber();
            if (safetyNumber) {
                safetyVerified = true;
                console.log(`[KEY] Safety fingerprint: ${safetyNumber}`);
                updateSafetyVerificationUI();
            }
            if (planBActive || connectionReadyShown) stopConnectionTimers();
            syncTransportState("shared_key_ready", false);
        } catch (err) {
            console.error("[KEY] Error deriving shared key:", err);
        }
    } else if (msg.type === "transport_state") {
        remoteTransportState = msg.data?.state || null;
        announceTransportState(remoteTransportState, false);
        if (
            remoteTransportState?.planBActive ||
            remoteTransportState?.mode === "plan_b"
        ) {
            activatePlanBFromRemote();
        }
    } else if (msg.type === "peer_disconnected") {
        addSystemMessage("Собеседник покинул чат");
        setTimeout(() => {
            window.exitChat();
            window.location.reload();
        }, 2000);
    }
};

window._exitChatInternal = function () {
    clearTimeout(connectionTimeout);
    resetConnectionReadiness();
    setConnectionOverlayVisible(false);
    if (messages) {
        messages.forEach((msg) => {
            if (msg.url && msg.url.startsWith("blob:"))
                URL.revokeObjectURL(msg.url);
        });
    }
    if (dataChannel) dataChannel.close();
    if (fileChannel) fileChannel.close();
    if (pc) pc.close();
    messages = [];
    sharedKey = null;
    myKeyPair = null;
    peerId = null;
    peerNickname = null;
    window.currentChatPeer = null;
    if (typeof window.cleanupActiveCall === "function")
        window.cleanupActiveCall(false);
    pc = null;
    dataChannel = null;
    fileChannel = null;
    removeFileTransferProgressElement();
    currentFileTransfer = null;
    planBReceivingFiles = {};
    planBOutgoingFileTransfer = null;
    binaryFileSupported = false;
    connectionAttempts = 0;
    forceTurn = true;
    usingTurn = false;
    const chatDiv = document.getElementById("chat");
    const lobbyDiv = document.getElementById("lobby");
    const connectingDiv = document.getElementById("connectingView");
    if (chatDiv) chatDiv.style.display = "none";
    if (connectingDiv) connectingDiv.style.display = "none";
    if (lobbyDiv) lobbyDiv.style.display = "flex";
    const messagesDiv = document.getElementById("messages");
    if (messagesDiv) messagesDiv.innerHTML = "";
    updateChatStatus("");
};

window.exitChat = function () {
    if (ws && ws.readyState === WebSocket.OPEN)
        ws.send(JSON.stringify({ type: "peer_disconnected" }));
    window._exitChatInternal();
    window.location.reload();
};

window.sendMessage = sendMessage;
window.sendFile = sendFile;

// Обработчик скачивания файлов через ссылки
document.addEventListener("click", (e) => {
    const downloadLink = e.target.closest(".file-download-link");
    if (downloadLink) {
        e.preventDefault();
        const a = document.createElement("a");
        a.href = downloadLink.dataset.url;
        a.download = downloadLink.dataset.filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
    }
});

document.addEventListener("visibilitychange", flushReadReceipts);

updateSafetyVerificationUI();
