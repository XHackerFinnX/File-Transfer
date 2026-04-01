let file;
let downloaded = false;
const CHUNK = 64 * 1024;
const pendingCandidates = [];

const drop = document.getElementById("drop");
const fileInput = document.getElementById("fileInput");
const timeSelect = document.getElementById("time");
const linkEl = document.getElementById("link");
const bar = document.getElementById("bar");
const statusEl = document.getElementById("status");

// ==================== TURN Credentials ====================
async function getTurnServers() {
    try {
        const res = await fetch("/turn-credentials");
        if (!res.ok) throw new Error("TURN недоступен");
        const { username, credential, urls } = await res.json();
        console.log("TURN доступен");
        return [
            { urls: "stun:stun.l.google.com:19302" },
            { urls, username, credential },
        ];
    } catch (err) {
        console.warn("⚠️ TURN недоступен, используем только STUN", err);
        return [{ urls: "stun:stun.l.google.com:19302" }];
    }
}

// ==================== Выбор файла ====================
drop.addEventListener("click", () => fileInput.click());

fileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) {
        file = e.target.files[0];
        updateDropUI(file);
    }
});

drop.ondrop = (e) => {
    e.preventDefault();
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
        file = e.dataTransfer.files[0];
        updateDropUI(file);
    }
};
drop.ondragover = (e) => e.preventDefault();

// ==================== UI при выборе файла ====================
function updateDropUI(selectedFile) {
    const dropText = drop.querySelector(".drop-text");
    const dropHint = drop.querySelector(".drop-hint");
    const dropIcon = drop.querySelector(".drop-icon");

    if (dropText) dropText.textContent = selectedFile.name;
    if (dropHint)
        dropHint.innerHTML = `Файл выбран <span style="color:#22c55e"></span>`;

    if (dropIcon) {
        dropIcon.innerHTML = `
        <svg fill="#3b82f6" xmlns="http://www.w3.org/2000/svg" 
            width="24" height="24" viewBox="630 796 200 200">
        <path d="M787.116,872.255h-4.284v-23.424C782.832,819.699,759.133,796,730,796c-29.13,0-52.83,23.699-52.83,52.831v23.424h-4.287
            c-11.133,0-20.159,9.025-20.159,20.159v62.589c0,22.642,18.354,40.997,40.996,40.997h72.559c22.642,0,40.996-18.355,40.996-40.997
            v-62.589C807.275,881.28,798.25,872.255,787.116,872.255z M737.506,934.545v25.462c0,4.145-3.361,7.506-7.506,7.506
            s-7.506-3.361-7.506-7.506v-25.462c-5.718-2.788-9.667-8.639-9.667-15.428c0-9.484,7.689-17.174,17.173-17.174
            c9.485,0,17.174,7.689,17.174,17.174C747.174,925.906,743.223,931.758,737.506,934.545z M763.726,872.255h-67.448v-23.424
            c0-18.596,15.128-33.725,33.723-33.725c18.597,0,33.725,15.129,33.725,33.725V872.255z"/>
        </svg>                    
    `;
        dropIcon.style.background =
            "linear-gradient(135deg, rgba(59, 130, 246, 0.25), rgba(139, 92, 246, 0.25))";
    }

    drop.style.borderStyle = "solid";
    drop.style.borderColor = "#3b82f6";
    drop.style.background = "rgba(59, 130, 246, 0.08)";
}

// ==================== Создание комнаты и передача ====================
async function create() {
    if (!file) return alert("Сначала выберите файл!");

    statusEl.innerText = "Создаём комнату...";
    bar.style.width = "0%";

    const res = await fetch(
        `${location.origin}/file/create?minutes=${timeSelect.value}`,
        { method: "POST" },
    );

    if (!res.ok) {
        statusEl.innerText = `❌ Ошибка создания комнаты: ${res.status}`;
        console.error("Ошибка:", await res.text());
        return;
    }

    const { room_id } = await res.json();

    if (!room_id) {
        statusEl.innerText = "❌ Не получен room_id";
        return;
    }

    // Генерация ключа шифрования
    const key = await crypto.subtle.generateKey(
        { name: "AES-GCM", length: 256 },
        true,
        ["encrypt", "decrypt"],
    );
    const rawKey = await crypto.subtle.exportKey("raw", key);
    const keyBase64 = btoa(String.fromCharCode(...new Uint8Array(rawKey)));

    const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
        `${wsProtocol}//${location.host}/file/ws/${room_id}`,
    );

    // Получаем динамические учетные данные TURN
    const iceServers = await getTurnServers();
    const pc = new RTCPeerConnection({
        iceServers,
        iceCandidatePoolSize: 10,
    });

    pc.onicegatheringstatechange = () => {
        console.log("[ICE] Gathering state:", pc.iceGatheringState);
    };

    const ch = pc.createDataChannel("file");

    // ==================== ICE ====================
    pc.onicecandidate = (e) => {
        if (e.candidate) {
            console.log(
                `[ICE] Sender candidate: ${e.candidate.type} | ${e.candidate.candidate}`,
            );
            ws.send(JSON.stringify({ candidate: e.candidate }));
        } else {
            console.log("[ICE] All candidates gathered (end of candidates)");
        }
    };

    pc.oniceconnectionstatechange = () => {
        const state = pc.iceConnectionState;
        console.log("[ICE] Sender iceConnectionState:", state);

        if (state === "failed" || state === "disconnected") {
            statusEl.innerHTML = `❌ Не удалось установить соединение.<br>Попробуйте обновить страницы и создать новую ссылку.`;
        }
    };

    pc.onconnectionstatechange = () =>
        console.log("[PC] Sender connectionState:", pc.connectionState);

    // ==================== Signaling ====================
    ws.onopen = () => console.log("[WS] Sender connected");

    ws.onmessage = async (e) => {
        const m = JSON.parse(e.data);

        if (m.answer) {
            console.log("[SIGNAL] Received answer from receiver");
            await pc.setRemoteDescription(m.answer);

            for (const c of pendingCandidates) {
                await pc.addIceCandidate(c);
            }
            pendingCandidates.length = 0;
        } else if (m.type === "receiver_joined") {
            console.log("[SIGNAL] Receiver joined → creating offer");
            statusEl.innerText = "Подключаемся к получателю...";
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            ws.send(JSON.stringify({ offer: pc.localDescription }));
        } else if (m.candidate) {
            console.log("[ICE] Received candidate from receiver");
            if (pc.remoteDescription) {
                await pc.addIceCandidate(m.candidate);
            } else {
                pendingCandidates.push(m.candidate);
            }
        }
    };

    // ==================== Передача файла ====================
    ch.onopen = async () => {
        console.log("[DC] DataChannel OPEN — начинаем передачу");
        statusEl.innerText = "Шифруем и передаём файл...";
        bar.style.width = "0%";

        const iv = crypto.getRandomValues(new Uint8Array(12));
        const encrypted = await crypto.subtle.encrypt(
            { name: "AES-GCM", iv },
            key,
            await file.arrayBuffer(),
        );

        ch.send(
            JSON.stringify({
                name: file.name,
                size: encrypted.byteLength,
                iv: Array.from(iv),
            }),
        );

        let offset = 0;
        const data = new Uint8Array(encrypted);

        while (offset < data.length) {
            while (ch.bufferedAmount > 1_000_000)
                await new Promise((r) => setTimeout(r, 10));

            const chunk = data.slice(offset, offset + CHUNK);
            ch.send(chunk);
            offset += CHUNK;

            bar.style.width = Math.round((offset / data.length) * 100) + "%";
        }

        ch.send("EOF");
        statusEl.innerText = "✅ Файл успешно отправлен!";
        console.log("[DC] Файл полностью отправлен");
    };

    // Показываем ссылку
    linkEl.innerHTML = `<strong>${location.origin}/file/receiver?room=${room_id}#key=${keyBase64}</strong>`;
    linkEl.style.color = "#22c55e";
}
