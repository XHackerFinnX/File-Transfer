// P2P Chat with E2E Encryption
let pc,
    dataChannel,
    myKeyPair,
    sharedKey,
    peerId,
    peerNickname,
    messages = [];

async function getTurnServers() {
    try {
        const res = await fetch("/turn-credentials");
        if (!res.ok) throw new Error("TURN недоступен");
        const { username, credential, urls } = await res.json();
        console.log("TURN доступен");
        return [
            { urls: "stun:stun.l.google.com:19302" },
            { urls: "stun:stun1.l.google.com:19302" },
            { urls, username, credential },
        ];
    } catch (err) {
        console.warn("⚠️ TURN недоступен, используем только STUN", err);
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

    updateChatStatus("Устанавливается защищенное соединение...");

    // Получаем временные учетные данные перед созданием PC
    const iceServers = await getTurnServers();

    pc = new RTCPeerConnection({
        iceServers,
        iceCandidatePoolSize: 10,
    });

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
    } catch (err) {
        console.error("[v0] Error generating keys:", err);
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
        } catch (err) {
            console.error("[v0] Error creating offer:", err);
            updateChatStatus("Ошибка создания соединения");
        }
    } else {
        pc.ondatachannel = (e) => {
            dataChannel = e.channel;
            setupDataChannel();
        };
    }

    pc.onicecandidate = (e) => {
        if (e.candidate) {
            ws.send(
                JSON.stringify({
                    type: "candidate",
                    data: { to: peerId, candidate: e.candidate },
                }),
            );
        }
    };

    pc.onconnectionstatechange = () => {
        switch (pc.connectionState) {
            case "connected":
                updateChatStatus("");
                addSystemMessage("Защищенное соединение установлено");
                break;
            case "disconnected":
                updateChatStatus("Соединение потеряно. Переподключение...");
                break;
            case "failed":
                updateChatStatus("Не удалось установить соединение");
                break;
        }
    };
};

function setupDataChannel() {
    dataChannel.onopen = () => {
        console.log("[DC] Data channel открыт");
        updateChatStatus("");
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
                    console.warn("Получено сообщение до согласования ключей");
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
            console.error("[v0] Error decrypting message:", err);
        }
    };
}

window.handleWebRTCMessage = async function (msg) {
    if (msg.type === "offer") {
        try {
            if (!pc) return;
            await pc.setRemoteDescription(msg.data.offer);
            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);
            ws.send(
                JSON.stringify({
                    type: "answer",
                    data: { to: msg.data.from, answer: pc.localDescription },
                }),
            );
        } catch (err) {
            console.error("Error handling offer:", err);
        }
    } else if (msg.type === "answer") {
        try {
            if (!pc) return;
            await pc.setRemoteDescription(msg.data.answer);
        } catch (err) {
            console.error("Error handling answer:", err);
        }
    } else if (msg.type === "candidate") {
        try {
            if (pc && msg.data.candidate) {
                await pc.addIceCandidate(msg.data.candidate);
            }
        } catch (err) {
            console.error("Error adding ICE candidate:", err);
        }
    } else if (msg.type === "public_key") {
        try {
            if (!myKeyPair) return;
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
            addSystemMessage("Ключи шифрования согласованы");
        } catch (err) {
            console.error("Error deriving shared key:", err);
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
        updateChatStatus("Соединение еще не установлено");
        return;
    }

    if (!sharedKey) {
        updateChatStatus("Ключи шифрования еще не согласованы");
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
        console.error("[v0] Error sending message:", err);
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
