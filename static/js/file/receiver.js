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

const params = new URLSearchParams(location.search);
const room = params.get("room");
const keyBase64 = location.hash.split("key=")[1];
const pendingCandidates = [];

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
const ws = new WebSocket(`${wsProtocol}//${location.host}/file/ws/${room}`);

// Получаем динамические учетные данные TURN
(async () => {
    const iceServers = await getTurnServers();
    pc = new RTCPeerConnection({
        iceServers,
        iceCandidatePoolSize: 10,
    });

    pc.onicegatheringstatechange = () => {
        console.log("[ICE] Gathering state:", pc.iceGatheringState);
    };

    pc.onicecandidate = (e) => {
        if (e.candidate) {
            ws.send(JSON.stringify({ candidate: e.candidate }));
        }
    };

    pc.oniceconnectionstatechange = () => {
        const state = pc.iceConnectionState;
        if ((state === "failed" || state === "disconnected") && !downloaded) {
            bar.style.background = "linear-gradient(90deg, #f87171, #ef4444)";
            statusEl.innerHTML =
                "❌ Не удалось установить P2P-соединение.<br>Попробуйте обновить обе страницы.";
        }
    };

    pc.ondatachannel = (e) => {
        const ch = e.channel;
        ch.onmessage = handleDataChannelMessage;
    };
})();

let pc = null;
let meta,
    buf = [],
    size = 0;
let downloaded = false;

const bar = document.getElementById("bar");
const statusEl = document.getElementById("status");

// ==================== iOS DETECT ====================
const isIOS =
    /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

// ==================== DOWNLOAD HELPER ====================
function triggerDownload(blob, filename) {
    const url = URL.createObjectURL(blob);

    // Android / Desktop (normal flow)
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.style.display = "none";
    document.body.appendChild(a);

    try {
        a.click();
    } catch (e) {
        console.log("[DOWNLOAD] click failed:", e);
    }

    // iOS fallback (critical)
    if (isIOS) {
        setTimeout(() => {
            window.open(url, "_blank");
        }, 100);
    }

    setTimeout(() => {
        URL.revokeObjectURL(url);
        a.remove();
    }, 60000);
}

// ==================== DATA CHANNEL ====================
function handleDataChannelMessage(ev) {
    if (typeof ev.data === "string") {
        if (ev.data === "EOF") {
            keyPromise.then(async () => {
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

                triggerDownload(blob, meta.name);

                statusEl.innerText = "✅ Скачано!";
                downloaded = true;
                ws.close();
            });
            return;
        }

        meta = JSON.parse(ev.data);
        statusEl.innerText = `Получаем ${meta.name}...`;
        return;
    }

    buf.push(ev.data);
    size += ev.data.byteLength;

    bar.style.width = Math.round((size / meta.size) * 100) + "%";
}

// ==================== SIGNALING ====================
ws.onmessage = async (e) => {
    const m = JSON.parse(e.data);

    if (!pc) return; // Ждём инициализации PC

    if (m.offer) {
        await pc.setRemoteDescription(m.offer);

        for (const c of pendingCandidates) {
            await pc.addIceCandidate(c);
        }
        pendingCandidates.length = 0;

        const ans = await pc.createAnswer();
        await pc.setLocalDescription(ans);

        ws.send(JSON.stringify({ answer: pc.localDescription }));
    } else if (m.candidate) {
        if (pc.remoteDescription) {
            await pc.addIceCandidate(m.candidate);
        } else {
            pendingCandidates.push(m.candidate);
        }
    } else if (m.type === "peer_disconnected") {
        if (!downloaded) {
            bar.style.background = "linear-gradient(90deg, #f87171, #ef4444)";
            statusEl.innerHTML =
                "❌ Отправитель закрыл страницу.<br>Передача отменена.";
        }
    }
};

ws.onclose = () => {
    if (!downloaded) {
        bar.style.background = "linear-gradient(90deg, #f87171, #ef4444)";
        statusEl.innerHTML =
            "❌ Отправитель закрыл страницу.<br>Передача отменена.";
    }
};

ws.onopen = () => console.log("[WS] Receiver connected");
