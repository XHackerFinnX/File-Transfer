const params = new URLSearchParams(location.search);
const room = params.get("room");
const keyBase64 = location.hash.split("key=")[1];

if (!room || !keyBase64) {
    document.getElementById("status").innerText = "❌ Неправильная ссылка!";
    throw new Error("Missing room or key");
}

const raw = Uint8Array.from(atob(keyBase64), (c) => c.charCodeAt(0));
let key;
const keyPromise = crypto.subtle
    .importKey("raw", raw, { name: "AES-GCM" }, false, ["decrypt"])
    .then((k) => {
        key = k;
        return k;
    });

const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
const ws = new WebSocket(`${wsProtocol}//${location.host}/ws/${room}`);
const pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
});

let meta,
    buf = [],
    size = 0;
let downloaded = false; // ← новая переменная
const bar = document.getElementById("bar");
const statusEl = document.getElementById("status");

// ==================== ICE ====================
pc.onicecandidate = (e) => {
    if (e.candidate) ws.send(JSON.stringify({ candidate: e.candidate }));
};

pc.onconnectionstatechange = () =>
    console.log("[PC] Receiver connection state:", pc.connectionState);

// ==================== DATA CHANNEL ====================
pc.ondatachannel = (e) => {
    const ch = e.channel;

    ch.onmessage = async (ev) => {
        if (typeof ev.data === "string") {
            if (ev.data === "EOF") {
                await keyPromise;
                // ... (весь код расшифровки и скачивания остаётся без изменений)

                const merged = new Uint8Array(size);
                let offset = 0;
                for (const b of buf) {
                    merged.set(new Uint8Array(b), offset);
                    offset += b.byteLength;
                }

                const iv = new Uint8Array(meta.iv);
                const dec = await crypto.subtle.decrypt(
                    { name: "AES-GCM", iv },
                    key,
                    merged,
                );

                const blob = new Blob([dec]);
                const a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = meta.name;
                a.click();

                statusEl.innerText = "✅ Скачано!";
                downloaded = true;
                ws.close();
                return;
            }
            meta = JSON.parse(ev.data);
            statusEl.innerText = `Получаем ${meta.name}...`;
            return;
        }

        buf.push(ev.data);
        size += ev.data.byteLength;
        bar.style.width = Math.round((size / meta.size) * 100) + "%";
    };
};

// ==================== SIGNALING ====================
ws.onmessage = async (e) => {
    const m = JSON.parse(e.data);

    if (m.offer) {
        await pc.setRemoteDescription(m.offer);
        const ans = await pc.createAnswer();
        await pc.setLocalDescription(ans);
        ws.send(JSON.stringify({ answer: pc.localDescription }));
    } else if (m.candidate) {
        await pc.addIceCandidate(m.candidate);
    }
    // === НОВОЕ: обработка отключения отправителя ===
    else if (m.type === "peer_disconnected") {
        if (!downloaded) {
            bar.style.background = "linear-gradient(90deg, #f87171, #ef4444)";
            statusEl.innerHTML = `❌ Отправитель закрыл страницу.<br>Передача отменена.`;
            console.log("[SIGNAL] Sender disconnected");
        }
    }
};

ws.onclose = () => {
    if (!downloaded) {
        bar.style.background = "linear-gradient(90deg, #f87171, #ef4444)";
        statusEl.innerHTML = `❌ Отправитель закрыл страницу.<br>Передача отменена.`;
    }
};

ws.onopen = () => console.log("[WS] Receiver connected");
