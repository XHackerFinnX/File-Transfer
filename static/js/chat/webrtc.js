// P2P Chat with E2E Encryption + Optimized File Transfer (binary with fallback)
let pc,
    dataChannel,
    fileChannel,
    myKeyPair,
    sharedKey,
    peerId,
    peerNickname,
    messages = [];
let connectionTimeout = null;

let connectionAttempts = 0;
const MAX_TURN_ATTEMPTS = 3;
let forceTurn = true;

// Для бинарной передачи
let currentFileTransfer = null;
let fileTransferProgressElement = null;
let nextFileId = 1;
let receivingFiles = {};
let binaryFileSupported = false;
let fileChannelReady = false;

async function getIceServers() {
    try {
        if (!forceTurn) {
            console.warn("⚠️ Используем только STUN (fallback)");
            return [
                { urls: "stun:stun.l.google.com:19302" },
                { urls: "stun:stun1.l.google.com:19302" },
            ];
        }

        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 3000);

        const res = await fetch("/turn-credentials", {
            signal: controller.signal,
        });

        clearTimeout(timeoutId);

        if (!res.ok) throw new Error(`TURN error ${res.status}`);

        const { username, credential, urls } = await res.json();

        console.log("✅ Используем TURN (приоритет)");

        return [
            {
                urls,
                username,
                credential,
            },
        ];
    } catch (err) {
        console.warn("❌ TURN недоступен → fallback на STUN");
        forceTurn = false;

        return [
            { urls: "stun:stun.l.google.com:19302" },
            { urls: "stun:stun1.l.google.com:19302" },
        ];
    }
}

function isImageFile(mimeType) {
    return mimeType.startsWith("image/");
}

// ========== ЗАПУСК ЧАТА ==========
window.startChat = async function (data) {
    peerId = data.peer_id;
    peerNickname = data.peer_nickname;
    const role = data.role;
    window.receivingFile = null;
    messages = [];

    updateChatStatus("Устанавливается защищённое соединение...");
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

    const iceServers = await getIceServers();

    pc = new RTCPeerConnection({
        iceServers,
        iceCandidatePoolSize: 10,
        iceTransportPolicy: forceTurn ? "relay" : "all",
        bundlePolicy: "max-bundle",
        rtcpMuxPolicy: "require",
    });

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
                clearTimeout(connectionTimeout);

                connectionAttempts++;
                console.warn(`❌ ICE failed (попытка ${connectionAttempts})`);

                if (connectionAttempts < MAX_TURN_ATTEMPTS && forceTurn) {
                    console.log("🔄 Повторная попытка через TURN...");

                    setTimeout(() => {
                        window.startChat({
                            peer_id: peerId,
                            peer_nickname: peerNickname,
                            role: role,
                        });
                    }, 1000);

                    return;
                }

                console.warn("⚠️ TURN не работает → переключаемся на STUN");
                forceTurn = false;
                connectionAttempts = 0;

                setTimeout(() => {
                    window.startChat({
                        peer_id: peerId,
                        peer_nickname: peerNickname,
                        role: role,
                    });
                }, 1000);

                updateChatStatus("Переключаемся на резервное соединение...");
                break;
            case "disconnected":
                updateChatStatus("Соединение потеряно");
                break;
        }
    };
    pc.onconnectionstatechange = () =>
        console.log("[PC] connectionState:", pc.connectionState);
    pc.onicegatheringstatechange = () =>
        console.log("[ICE] gatheringState:", pc.iceGatheringState);

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
};

function setupDataChannel() {
    dataChannel.onopen = () => {
        console.log("[DC] ✅ Основной канал открыт");
        clearTimeout(connectionTimeout);
        updateChatStatus("");
        if (!sharedKey)
            addSystemMessage(
                "Ключи шифрования ещё не согласованы. Ожидаем обмен ключами...",
            );
    };
    dataChannel.onclose = () => {
        console.log("[DC] Основной канал закрыт");
        addSystemMessage("Собеседник отключился");
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
                const encrypted = Uint8Array.from(atob(data.encrypted), (c) =>
                    c.charCodeAt(0),
                );
                const decrypted = await crypto.subtle.decrypt(
                    { name: "AES-GCM", iv },
                    sharedKey,
                    encrypted,
                );
                const text = new TextDecoder().decode(decrypted);
                const messageId = crypto.randomUUID();
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
        if (event.data.byteLength < 8) return;
        const view = new DataView(event.data);
        const fileId = view.getUint32(0);
        const offset = view.getUint32(4);
        const chunkData = event.data.slice(8);

        const receiving = receivingFiles[fileId];
        if (!receiving) {
            console.warn(
                `[FILE] Получен чанк для неизвестного fileId ${fileId}`,
            );
            return;
        }

        receiving.chunks[offset] = new Uint8Array(chunkData);
        receiving.received += chunkData.byteLength;
        const progress = Math.round(
            (receiving.received / receiving.size) * 100,
        );
        updateChatStatus(`📥 ${progress}%`);

        if (receiving.received === receiving.size) {
            await finalizeBinaryFileReceive(receiving);
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
    const decrypted = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: receiving.iv },
        sharedKey,
        merged,
    );
    const blob = new Blob([decrypted], { type: receiving.mimeType });
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
    const decrypted = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: receiving.iv },
        sharedKey,
        merged,
    );
    const blob = new Blob([decrypted], { type: receiving.mimeType });
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
    if (!dataChannel || dataChannel.readyState !== "open") {
        updateChatStatus("Соединение ещё не установлено");
        return;
    }
    if (!sharedKey) {
        updateChatStatus("Ключи шифрования ещё не согласованы");
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
}

async function sendFileBinary(file) {
    const fileId = nextFileId++;
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const fileData = await file.arrayBuffer();
    const encrypted = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv },
        sharedKey,
        fileData,
    );
    const isImage = isImageFile(file.type);
    const totalSize = encrypted.byteLength;
    const CHUNK = 128 * 1024; // 128 КБ – стабильнее, чем 256

    dataChannel.send(
        JSON.stringify({
            type: "file_metadata",
            binary: true,
            fileId: fileId,
            name: file.name,
            size: totalSize,
            iv: Array.from(iv),
            mimeType: file.type || "application/octet-stream",
            isImage: isImage,
        }),
    );

    // Прогресс-бар
    fileTransferProgressElement = document.createElement("div");
    fileTransferProgressElement.className = "file-progress-container";
    fileTransferProgressElement.innerHTML = `
        <div class="file-progress-bar"><div class="file-progress-fill" id="fileProgressFill"></div></div>
        <div class="file-progress-info" id="fileProgressInfo">0%</div>
        <button class="file-progress-cancel" id="cancelFileTransfer">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
        </button>
    `;
    document
        .querySelector(".message-input-wrapper")
        .before(fileTransferProgressElement);

    let cancelled = false;
    const cancelHandler = () => {
        cancelled = true;
        // Отправляем сигнал отмены через основной канал (JSON)
        if (dataChannel && dataChannel.readyState === "open") {
            dataChannel.send(
                JSON.stringify({ type: "file_cancel", fileId: fileId }),
            );
        }
        if (fileTransferProgressElement) fileTransferProgressElement.remove();
        updateChatStatus("передача файла отменена");
    };
    document
        .getElementById("cancelFileTransfer")
        .addEventListener("click", cancelHandler);
    currentFileTransfer = { cancel: cancelHandler };

    const data = new Uint8Array(encrypted);
    let offset = 0;

    while (offset < data.length && !cancelled) {
        // Ждём, пока буфер не переполнится
        while (fileChannel.bufferedAmount > 2_000_000 && !cancelled) {
            await new Promise((r) => setTimeout(r, 10));
        }
        if (cancelled) break;

        const chunk = data.slice(offset, offset + CHUNK);
        const header = new ArrayBuffer(8);
        const headerView = new DataView(header);
        headerView.setUint32(0, fileId);
        headerView.setUint32(4, offset);
        const combined = new Uint8Array(header.byteLength + chunk.byteLength);
        combined.set(new Uint8Array(header), 0);
        combined.set(chunk, header.byteLength);
        fileChannel.send(combined.buffer);

        offset += CHUNK;
        const progress = Math.round((offset / data.length) * 100);
        const progressFill = document.getElementById("fileProgressFill");
        const progressInfo = document.getElementById("fileProgressInfo");
        if (progressFill) progressFill.style.width = `${progress}%`;
        if (progressInfo)
            progressInfo.textContent = `${(offset / 1024 / 1024).toFixed(1)} MB / ${(data.length / 1024 / 1024).toFixed(1)} MB`;
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
        dataChannel.send(JSON.stringify({ type: "file_end", fileId: fileId }));
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

    if (fileTransferProgressElement) fileTransferProgressElement.remove();
    currentFileTransfer = null;
    updateChatStatus("");
}

async function sendFileJson(file) {
    fileTransferProgressElement = document.createElement("div");
    fileTransferProgressElement.className = "file-progress-container";
    fileTransferProgressElement.innerHTML = `
        <div class="file-progress-bar"><div class="file-progress-fill" id="fileProgressFill"></div></div>
        <div class="file-progress-info" id="fileProgressInfo">0%</div>
        <button class="file-progress-cancel" id="cancelFileTransfer">
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
        </button>
    `;
    document
        .querySelector(".message-input-wrapper")
        .before(fileTransferProgressElement);

    let cancelled = false;
    const cancelHandler = () => {
        cancelled = true;
        if (dataChannel && dataChannel.readyState === "open") {
            dataChannel.send(
                JSON.stringify({ type: "file_cancel", fileId: fileId }),
            );
        }
        if (fileTransferProgressElement) fileTransferProgressElement.remove();
        updateChatStatus("передача файла отменена");
    };
    document
        .getElementById("cancelFileTransfer")
        .addEventListener("click", cancelHandler);
    currentFileTransfer = { cancel: cancelHandler };

    const fileId = crypto.randomUUID();
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const fileData = await file.arrayBuffer();
    const encrypted = await crypto.subtle.encrypt(
        { name: "AES-GCM", iv },
        sharedKey,
        fileData,
    );
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

    while (offset < data.length && !cancelled) {
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
        const progressFill = document.getElementById("fileProgressFill");
        const progressInfo = document.getElementById("fileProgressInfo");
        if (progressFill) progressFill.style.width = `${progress}%`;
        if (progressInfo)
            progressInfo.textContent = `${(offset / 1024 / 1024).toFixed(1)} MB / ${(data.length / 1024 / 1024).toFixed(1)} MB`;
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

    if (fileTransferProgressElement) fileTransferProgressElement.remove();
    currentFileTransfer = null;
    updateChatStatus("");
}

// ========== ОСТАЛЬНЫЕ ФУНКЦИИ (отправка сообщений, рендер, статус) ==========
async function sendMessage() {
    const input = document.getElementById("messageInput");
    const text = input.value.trim();
    if (!text) return;

    const quote = document.querySelector(".message-quote");
    let replyToId = quote ? quote.dataset.replyToId : null;

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
                replyTo: replyToId,
            }),
        );
        const messageId = crypto.randomUUID();
        messages.push({
            id: messageId,
            from: "me",
            text: text,
            time: new Date().toLocaleTimeString("ru-RU", {
                hour: "2-digit",
                minute: "2-digit",
            }),
            replyTo: replyToId,
            senderName: "Вы",
        });
        renderMessages();
        input.value = "";
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
                    <div class="file-name">${escapeHtml(m.text)}</div>
                    <div class="file-size">${formatFileSize(m.size)}</div>
                    <span class="message-time">${m.time}</span>
                </div>
            `;
            }
            if (m.type === "file") {
                return `
                <div class="message ${m.from} file" data-message-id="${m.id}" data-url="${m.url || ""}" data-original-filename="${escapeAttr(m.text)}">
                    <div class="file-icon">
                        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m2.25 19.5H15m-6-15h3.75M15 10.5h3.75m-3.75 3.75H9.75m3.75-3.75H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125H18.375c.621 0 1.125-.504 1.125-1.125V11.25c0-.621-.504-1.125-1.125-1.125h-3.75m-4.5-3.75h3.75M9 5.625v3.75m3.5-3.75v.75m-6 0v3.75m3.75-6H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25c0-.621-.504-1.125-1.125-1.125h-3.75m-4.5-3.75H9.75m3.75 3.75H9.75" />
                        </svg>
                    </div>
                    <div class="file-name">
                        ${m.url && !m.isImage ? `<a href="#" class="file-download-link" data-filename="${escapeAttr(m.text)}" data-url="${m.url}">${escapeHtml(m.text)}</a>` : escapeHtml(m.text)}
                    </div>
                    <div class="file-size">${formatFileSize(m.size)}</div>
                    <span class="message-time">${m.time}</span>
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
            return `${quoteHtml}<div class="message ${m.from}" data-message-id="${m.id}"><span class="message-content">${parsedText}</span><span class="message-time">${m.time}</span></div>`;
        })
        .join("");
    container.scrollTop = container.scrollHeight;
}

function formatFileSize(bytes) {
    if (bytes === 0) return "0 Bytes";
    const k = 1024;
    const sizes = ["Bytes", "KB", "MB", "GB"];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
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
        typingTimeout = setTimeout(
            () => (statusEl.style.display = "none"),
            2000,
        );
    }
}

window.handleWebRTCMessage = async function (msg) {
    if (msg.type === "public_key_request" && myKeyPair) {
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
            if (pc) await pc.setRemoteDescription(msg.data.answer);
        } catch (err) {
            console.error("[SIGNAL] Error handling answer:", err);
        }
    } else if (msg.type === "candidate") {
        try {
            if (pc && msg.data.candidate)
                await pc.addIceCandidate(msg.data.candidate);
        } catch (err) {
            console.error("[ICE] Error adding candidate:", err);
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
            console.log("[KEY] ✅ Ключи шифрования согласованы");
            addSystemMessage("Ключи шифрования согласованы");
        } catch (err) {
            console.error("[KEY] Error deriving shared key:", err);
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
    pc = null;
    dataChannel = null;
    fileChannel = null;
    binaryFileSupported = false;
    connectionAttempts = 0;
    forceTurn = true;
    const chatDiv = document.getElementById("chat");
    const lobbyDiv = document.getElementById("lobby");
    if (chatDiv) chatDiv.style.display = "none";
    if (lobbyDiv) lobbyDiv.style.display = "block";
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
