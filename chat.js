// P2P Chat with E2E Encryption
let pc,
    dataChannel,
    myKeyPair,
    sharedKey,
    peerId,
    peerNickname,
    messages = [];
let connectionTimeout = null;

async function getTurnServers() {
    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3000);

        const res = await fetch("/turn-credentials", {
            signal: controller.signal,
        });
        clearTimeout(timeoutId);

        if (!res.ok) throw new Error(`TURN error ${res.status}`);
        const { username, credential, urls } = await res.json();
        console.log("✅ TURN credentials получены");
        return [
            { urls: "stun:stun.l.google.com:19302" },
            { urls: "stun:stun1.l.google.com:19302" },
            { urls, username, credential },
        ];
    } catch (err) {
        console.warn(
            "⚠️ TURN недоступен (",
            err.message || err,
            "), используем только STUN",
        );
        return [
            { urls: "stun:stun.l.google.com:19302" },
            { urls: "stun:stun1.l.google.com:19302" },
        ];
    }
}

// Вызывается из chat.html при start_connection
window.startChat = async function (data) {
    peerId = data.peer_id;
    peerNickname = data.peer_nickname;
    const role = data.role;

    updateChatStatus("Устанавливается защищённое соединение...");

    // Устанавливаем таймаут на 15 секунд
    clearTimeout(connectionTimeout);
    connectionTimeout = setTimeout(() => {
        if (
            pc &&
            pc.iceConnectionState !== "connected" &&
            pc.iceConnectionState !== "completed"
        ) {
            updateChatStatus(
                "Соединение устанавливается долго. Проверьте интернет или попробуйте перезайти.",
            );
            addSystemMessage(
                "Соединение не установлено за 15 секунд. Попробуйте выйти и создать новый чат.",
            );
        }
    }, 15000);

    // Получаем временные учетные данные перед созданием PC
    const iceServers = await getTurnServers();

    pc = new RTCPeerConnection({
        iceServers,
        iceCandidatePoolSize: 10,
    });

    // === КРИТИЧЕСКИ ВАЖНО: обрабатываем ICE состояние ===
    pc.oniceconnectionstatechange = () => {
        console.log("[ICE] iceConnectionState:", pc.iceConnectionState);
        switch (pc.iceConnectionState) {
            case "checking":
                updateChatStatus("Поиск пути для соединения...");
                break;
            case "connected":
            case "completed":
                clearTimeout(connectionTimeout);
                updateChatStatus("");
                addSystemMessage("Защищённое соединение установлено");
                // Если ключи ещё не согласованы — запросим повторно
                if (!sharedKey && role === "peer") {
                    console.log(
                        "[KEY] Ключи не согласованы, запрашиваем повторно",
                    );
                    ws.send(
                        JSON.stringify({
                            type: "public_key_request",
                            data: { to: peerId },
                        }),
                    );
                }
                break;
            case "failed":
                clearTimeout(connectionTimeout);
                updateChatStatus("Не удалось установить соединение");
                addSystemMessage(
                    "Соединение не установлено. Попробуйте выйти и создать новый чат.",
                );
                break;
            case "disconnected":
                updateChatStatus("Соединение потеряно");
                break;
        }
    };

    // Для совместимости (менее надёжный)
    pc.onconnectionstatechange = () => {
        console.log("[PC] connectionState:", pc.connectionState);
    };

    pc.onicegatheringstatechange = () => {
        console.log("[ICE] gatheringState:", pc.iceGatheringState);
    };

    try {
        myKeyPair = await crypto.subtle.generateKey(
            { name: "ECDH", namedCurve: "P-256" },
            true,
            ["deriveKey"],
        );
        const pub = btoa(
            String.fromCharCode(
                ...new Uint8Array(
                    await crypto.subtle.exportKey("raw", myKeyPair.publicKey),
                ),
            ),
        );
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
            console.log("[DC] DataChannel получен от удалённой стороны");
            dataChannel = e.channel;
            setupDataChannel();
        };
    }

    pc.onicecandidate = (e) => {
        if (e.candidate) {
            console.log(
                `[ICE] Кандидат (${e.candidate.type}): ${e.candidate.candidate.substring(0, 60)}...`,
            );
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
};

function setupDataChannel() {
    dataChannel.onopen = () => {
        console.log("[DC] ✅ Data channel открыт");
        clearTimeout(connectionTimeout);
        updateChatStatus("");
        if (!sharedKey) {
            addSystemMessage(
                "Ключи шифрования ещё не согласованы. Ожидаем обмен ключами...",
            );
        }
    };

    dataChannel.onclose = () => {
        console.log("[DC] Data channel закрыт");
        addSystemMessage("Собеседник отключился");
    };

    dataChannel.onerror = (err) => {
        console.error("[DC] Ошибка:", err);
        updateChatStatus("Ошибка соединения");
    };

    dataChannel.onmessage = async (e) => {
        try {
            const data = JSON.parse(e.data);
            if (data.type === "message") {
                if (!sharedKey) {
                    console.warn(
                        "[MSG] Получено сообщение до согласования ключей — игнорируем",
                    );
                    return;
                }
                const iv = new Uint8Array(data.iv);
                const encrypted = Uint8Array.from(atob(data.encrypted), (c) =>
                    c.charCodeAt(0),
                );
                const decrypted = await crypto.subtle.decrypt(
                    { name: "AES-GCM", iv },
                    sharedKey,
                    encrypted,
                );
                const text = new TextDecoder().decode(decrypted);
                messages.push({
                    from: "other",
                    text: text,
                    time: new Date().toLocaleTimeString("ru-RU", {
                        hour: "2-digit",
                        minute: "2-digit",
                    }),
                });
                renderMessages();
            } else if (data.type === "typing") {
                showTypingIndicator();
            }
        } catch (err) {
            console.error("[MSG] Error decrypting message:", err);
        }
    };
}

window.handleWebRTCMessage = async function (msg) {
    if (msg.type === "public_key_request" && myKeyPair) {
        console.log(
            "[KEY] Получен запрос на повторную отправку публичного ключа",
        );
        const pub = btoa(
            String.fromCharCode(
                ...new Uint8Array(
                    await crypto.subtle.exportKey("raw", myKeyPair.publicKey),
                ),
            ),
        );
        ws.send(
            JSON.stringify({
                type: "public_key",
                data: { to: msg.data.to, key: pub },
            }),
        );
        return;
    } else if (msg.type === "offer") {
        try {
            if (!pc) return;
            await pc.setRemoteDescription(msg.data.offer);
            console.log("[SIGNAL] Offer принят, создаём answer");
            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);
            ws.send(
                JSON.stringify({
                    type: "answer",
                    data: { to: msg.data.from, answer: pc.localDescription },
                }),
            );
            console.log("[SIGNAL] Answer отправлен");
        } catch (err) {
            console.error("[SIGNAL] Error handling offer:", err);
        }
    } else if (msg.type === "answer") {
        try {
            if (!pc) return;
            await pc.setRemoteDescription(msg.data.answer);
            console.log("[SIGNAL] Answer принят");
        } catch (err) {
            console.error("[SIGNAL] Error handling answer:", err);
        }
    } else if (msg.type === "candidate") {
        try {
            if (pc && msg.data.candidate) {
                await pc.addIceCandidate(msg.data.candidate);
                console.log(
                    `[ICE] Кандидат добавлен (${msg.data.candidate.type})`,
                );
            }
        } catch (err) {
            console.error("[ICE] Error adding candidate:", err);
        }
    } else if (msg.type === "public_key") {
        try {
            if (!myKeyPair) {
                console.warn(
                    "[KEY] Получен публичный ключ до генерации своих ключей",
                );
                return;
            }
            const theirPub = await crypto.subtle.importKey(
                "raw",
                Uint8Array.from(atob(msg.data.key), (c) => c.charCodeAt(0)),
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
            addSystemMessage("Ключи шифрования согласованы");
        } catch (err) {
            console.error("[KEY] Error deriving shared key:", err);
        }
    } else if (msg.type === "peer_disconnected") {
        addSystemMessage("Собеседник покинул чат");
        setTimeout(() => {
            window.exitChat();
        }, 2000);
    }
};

async function sendMessage() {
    const input = document.getElementById("messageInput");
    const text = input.value.trim();

    if (!text) return;

    if (!dataChannel || dataChannel.readyState !== "open") {
        updateChatStatus("Соединение ещё не установлено");
        return;
    }

    if (!sharedKey) {
        updateChatStatus("Ключи шифрования ещё не согласованы");
        return;
    }

    try {
        const iv = crypto.getRandomValues(new Uint8Array(12));
        const encoded = new TextEncoder().encode(text);
        const encrypted = await crypto.subtle.encrypt(
            { name: "AES-GCM", iv },
            sharedKey,
            encoded,
        );

        dataChannel.send(
            JSON.stringify({
                type: "message",
                iv: Array.from(iv),
                encrypted: btoa(
                    String.fromCharCode(...new Uint8Array(encrypted)),
                ),
            }),
        );

        messages.push({
            from: "me",
            text: text,
            time: new Date().toLocaleTimeString("ru-RU", {
                hour: "2-digit",
                minute: "2-digit",
            }),
        });
        renderMessages();
        input.value = "";
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
            if (m.system) {
                return `<div class="system-message">${escapeHtml(m.text)}</div>`;
            }
            return `
                <div class="message ${m.from}">
                    <span class="message-content">${escapeHtml(m.text)}</span><span class="message-time">${m.time}</span>
                </div>
            `;
        })
        .join("");

    container.scrollTop = container.scrollHeight;
}

function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
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
    const statusEl = document.getElementById("statusChat");
    if (statusEl) {
        statusEl.textContent = text;
        statusEl.style.display = text ? "block" : "none";
    }
}

let typingTimeout;
function showTypingIndicator() {
    const statusEl = document.getElementById("statusChat");
    if (statusEl && peerNickname) {
        statusEl.textContent = `${peerNickname} печатает...`;
        statusEl.style.display = "block";

        clearTimeout(typingTimeout);
        typingTimeout = setTimeout(() => {
            statusEl.style.display = "none";
        }, 2000);
    }
}

window._exitChatInternal = function () {
    clearTimeout(connectionTimeout);
    if (dataChannel) {
        dataChannel.close();
    }
    if (pc) {
        pc.close();
    }
    messages = [];
    sharedKey = null;
    myKeyPair = null;
    peerId = null;
    peerNickname = null;
    pc = null;
    dataChannel = null;

    const chatDiv = document.getElementById("chat");
    const lobbyDiv = document.getElementById("lobby");
    if (chatDiv) chatDiv.style.display = "none";
    if (lobbyDiv) lobbyDiv.style.display = "block";
    const messagesDiv = document.getElementById("messages");
    if (messagesDiv) messagesDiv.innerHTML = "";
    updateChatStatus("");
};

window.exitChat = function () {
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "peer_disconnected" }));
    }
    window._exitChatInternal();
};

window.sendMessage = sendMessage;
