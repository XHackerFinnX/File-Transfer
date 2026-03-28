let file;
let downloaded = false; // для совместимости с обработчиками

const drop = document.getElementById("drop");
const fileInput = document.getElementById("fileInput");
const timeSelect = document.getElementById("time");
const linkEl = document.getElementById("link");
const bar = document.getElementById("bar");
const statusEl = document.getElementById("status");

const CHUNK = 64 * 1024;

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
        dropHint.innerHTML = `Файл выбран <span style="color:#22c55e">✓</span>`;

    if (dropIcon) {
        dropIcon.innerHTML = `
            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="#3b82f6">
                <path stroke-linecap="round" stroke-linejoin="round" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2" />
            </svg>`;
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
        `${location.origin}/create?minutes=${timeSelect.value}`,
        { method: "POST" },
    );
    const { room_id } = await res.json();

    // Генерация ключа шифрования
    const key = await crypto.subtle.generateKey(
        { name: "AES-GCM", length: 256 },
        true,
        ["encrypt", "decrypt"],
    );
    const rawKey = await crypto.subtle.exportKey("raw", key);
    const keyBase64 = btoa(String.fromCharCode(...new Uint8Array(rawKey)));

    const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${wsProtocol}//${location.host}/ws/${room_id}`);

    const pc = new RTCPeerConnection({
        iceServers: [
            {
                urls: [
                    "turn:5.42.124.68:3478",
                    "turn:5.42.124.68:3478?transport=tcp",
                ],
                username: "turnuser",
                credential: "StrongPassword123!",
            },
        ],
        iceCandidatePoolSize: 10,
    });

    pc.onicegatheringstatechange = () => {
        console.log("[ICE] Gathering state:", pc.iceGatheringState);
    };

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

    const ch = pc.createDataChannel("file");

    // ==================== ICE ====================
    pc.onicecandidate = (e) => {
        if (e.candidate) {
            console.log("[ICE] Sender candidate:", e.candidate);
            ws.send(JSON.stringify({ candidate: e.candidate }));
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
        } else if (m.type === "receiver_joined") {
            console.log("[SIGNAL] Receiver joined → creating offer");
            statusEl.innerText = "Подключаемся к получателю...";
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            ws.send(JSON.stringify({ offer: pc.localDescription }));
        } else if (m.candidate) {
            console.log("[ICE] Received candidate from receiver");
            await pc.addIceCandidate(m.candidate);
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
    linkEl.innerHTML = `<strong>${location.origin}/receiver?room=${room_id}#key=${keyBase64}</strong>`;
    linkEl.style.color = "#22c55e";
}
