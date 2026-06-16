let ws,
    myClientId = "";
let pendingRequest = false;
const CHAT_SESSION_STORAGE_KEY = "p2p_chat_session_id";
let mobileFilePickerOpen = false;

function getChatSessionId() {
    let sessionId = localStorage.getItem(CHAT_SESSION_STORAGE_KEY);
    if (!sessionId) {
        sessionId = crypto.randomUUID
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random()}`;
        localStorage.setItem(CHAT_SESSION_STORAGE_KEY, sessionId);
    }
    return sessionId;
}

function updateViewportHeightVar() {
    const viewportHeight = window.visualViewport?.height || window.innerHeight;
    document.documentElement.style.setProperty(
        "--app-height",
        `${viewportHeight}px`,
    );
}

updateViewportHeightVar();
window.addEventListener("resize", updateViewportHeightVar);
window.visualViewport?.addEventListener("resize", updateViewportHeightVar);
window.visualViewport?.addEventListener("scroll", updateViewportHeightVar);

function setChatScreen(screen, text = "") {
    const lobby = document.getElementById("lobby");
    const connecting = document.getElementById("connectingView");
    const chat = document.getElementById("chat");
    if (lobby) lobby.style.display = screen === "lobby" ? "flex" : "none";
    if (connecting)
        connecting.style.display = screen === "connecting" ? "flex" : "none";
    if (chat) chat.style.display = screen === "chat" ? "flex" : "none";
    if (text) {
        const connectingText = document.getElementById("connectingText");
        if (connectingText) connectingText.textContent = text;
    }
}

window.setChatScreen = setChatScreen;

function connectWebSocket() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const sessionId = encodeURIComponent(getChatSessionId());
    ws = new WebSocket(
        `${protocol}//${location.host}/chat/ws?session=${sessionId}`,
    );
    window.ws = ws;

    ws.onmessage = async (e) => {
        const msg = JSON.parse(e.data);
        console.log("📨 Получено сообщение:", msg.type, msg.data);

        if (msg.type === "init") {
            myClientId = msg.data.client_id;
            if (msg.data.resumed) {
                console.log(
                    "🔄 Сессия чата восстановлена после мобильного фонового режима",
                );
            }
        } else if (msg.type === "users") {
            renderUsers(msg.data);
        } else if (msg.type === "incoming_request") {
            document.querySelectorAll(".btn-connect.loading").forEach((btn) => {
                btn.classList.remove("loading");
                btn.querySelector(".btn-loader").style.display = "none";
            });
            showConnectionRequestDialog(msg.data.from_nickname, msg.data.from);
        } else if (msg.type === "room_created") {
            console.log(
                "✅ Комната создана, переходим в чат и ожидаем собеседника",
            );
            document.getElementById("chatTitle").textContent =
                msg.data.title || "Мой чат";
            setChatScreen("chat");
            if (typeof window.prepareWaitingRoom === "function") {
                window.prepareWaitingRoom(msg.data);
            }
            const statusEl = document.getElementById("chatPeerStatusText");
            if (statusEl)
                statusEl.textContent =
                    "Ожидаем подключения другого пользователя...";
        } else if (msg.type === "start_connection") {
            document.querySelectorAll(".btn-connect.loading").forEach((btn) => {
                btn.classList.remove("loading");
                btn.querySelector(".btn-loader").style.display = "none";
            });
            console.log("🚀 start_connection получен! Синхронизируем P2P");
            setChatScreen(
                "connecting",
                "Устанавливаем соединение у обоих собеседников...",
            );
            document.getElementById("chatTitle").textContent =
                msg.data.peer_nickname || "Чат";

            if (typeof window.startChat === "function") {
                window.startChat(msg.data);
            }
        } else if (msg.type === "request_rejected") {
            document.querySelectorAll(".btn-connect.loading").forEach((btn) => {
                btn.classList.remove("loading");
                btn.querySelector(".btn-loader").style.display = "none";
            });
            alert("Владелец комнаты отклонил ваш запрос.");
            pendingRequest = false;
        } else if (msg.type === "request_failed") {
            document.querySelectorAll(".btn-connect.loading").forEach((btn) => {
                btn.classList.remove("loading");
                btn.querySelector(".btn-loader").style.display = "none";
            });
            // alert(`Ошибка: ${msg.data.reason}`);
            pendingRequest = false;
        } else if (
            [
                "offer",
                "answer",
                "candidate",
                "public_key",
                "public_key_request",
                "relay_message",
                "transport_state",
                "peer_disconnected",
                "peer_reconnected",
                "peer_left",
            ].includes(msg.type)
        ) {
            if (typeof window.handleWebRTCMessage === "function") {
                window.handleWebRTCMessage(msg);
            }
        } else if (
            [
                "call_request",
                "call_response",
                "call_signal",
                "call_ended",
                "call_state",
            ].includes(msg.type)
        ) {
            if (typeof window.handleCallMessage === "function") {
                window.handleCallMessage(msg);
            }
        }
    };

    ws.onclose = () => setTimeout(connectWebSocket, 2000);
    ws.onerror = (err) => console.error("WebSocket error:", err);
}

function showConnectionRequestDialog(fromNickname, fromId) {
    const dialog = document.createElement("div");
    dialog.className = "custom-dialog-overlay";
    dialog.innerHTML = `
        <div class="custom-dialog">
            <div class="dialog-title">Запрос на подключение</div>
            <div class="dialog-text"><strong>${escapeHtml(fromNickname)}</strong> хочет подключиться к вашему чату.</div>
            <div class="dialog-buttons">
                <button class="btn-dialog-reject">Отклонить</button>
                <button class="btn-dialog-accept">Принять</button>
            </div>
        </div>
    `;
    document.body.appendChild(dialog);

    function sendResponse(accepted) {
        document.body.removeChild(dialog);
        ws.send(
            JSON.stringify({
                type: "request_response",
                data: { to: fromId, accepted: accepted },
            }),
        );
    }

    dialog.querySelector(".btn-dialog-accept").onclick = () =>
        sendResponse(true);
    dialog.querySelector(".btn-dialog-reject").onclick = () =>
        sendResponse(false);

    dialog.onclick = (e) => {
        if (e.target === dialog) {
            document.body.removeChild(dialog);
        }
    };

    if (!document.getElementById("dialog-styles")) {
        const style = document.createElement("style");
        style.id = "dialog-styles";
        style.textContent = `
            .custom-dialog-overlay {
                position: fixed;
                top: 0; left: 0; right: 0; bottom: 0;
                background: rgba(0, 0, 0, 0.7);
                display: flex;
                align-items: center;
                justify-content: center;
                z-index: 10000;
            }
            .custom-dialog {
                background: rgba(30, 30, 45, 0.95);
                border-radius: 16px;
                padding: 16px;
                width: 90%;
                max-width: 400px;
                text-align: center;
                box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5),
                            0 0 40px rgba(59, 130, 246, 0.15);
                border: 1px solid rgba(255, 255, 255, 0.08);
            }
            .dialog-title {
                font-size: 18px;
                font-weight: 600;
                color: #f1f5f9;
                margin-bottom: 12px;
            }
            .dialog-text {
                font-size: 14px;
                color: #94a3b8;
                margin-bottom: 16px;
                line-height: 1.5;
            }
            .dialog-buttons {
                display: flex;
                gap: 12px;
                justify-content: center;
            }
            .btn-dialog-reject,
            .btn-dialog-accept {
                flex: 1;
                padding: 12px;
                border-radius: 12px;
                font-weight: 600;
                font-size: 14px;
                cursor: pointer;
                transition: all 0.2s ease;
            }
            .btn-dialog-reject {
                background: rgba(239, 68, 68, 0.1);
                color: #ef4444;
                border: 1px solid rgba(239, 68, 68, 0.3);
            }
            .btn-dialog-reject:hover {
                background: rgba(239, 68, 68, 0.2);
            }
            .btn-dialog-accept {
                background: linear-gradient(135deg, #3b82f6, #6366f1);
                color: white;
                border: none;
                box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
            }
            .btn-dialog-accept:hover {
                transform: translateY(-1px);
                box-shadow: 0 6px 16px rgba(59, 130, 246, 0.4);
            }
        `;
        document.head.appendChild(style);
    }
}

function setNickname() {
    const nick = document.getElementById("nickname").value.trim();
    if (ws) {
        ws.send(
            JSON.stringify({
                type: "set_nickname",
                data: { nickname: nick },
            }),
        );
    }
}

function createRoom() {
    const title = document.getElementById("roomTitle").value.trim();
    if (ws) {
        ws.send(JSON.stringify({ type: "create_room", data: { title } }));
        document.getElementById("roomTitle").value = "";
    }
}

function connectToUser(targetId, buttonElement) {
    if (pendingRequest) {
        alert("Подождите, предыдущий запрос ещё обрабатывается.");
        return;
    }

    console.log("🔗 Клик по кнопке Войти →", targetId);
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        alert("Нет соединения с сервером. Обновите страницу.");
        return;
    }

    if (buttonElement) {
        buttonElement.classList.add("loading");
        buttonElement.querySelector(".btn-loader").style.display = "block";
    }

    pendingRequest = true;
    ws.send(
        JSON.stringify({
            type: "connect_request",
            data: { target_id: targetId },
        }),
    );

    setTimeout(() => {
        pendingRequest = false;
        if (buttonElement) {
            buttonElement.classList.remove("loading");
            buttonElement.querySelector(".btn-loader").style.display = "none";
        }
    }, 10000);
}

function renderUsers(list) {
    document.getElementById("count").textContent = list.length;
    const container = document.getElementById("usersList");

    if (!list.length) {
        container.innerHTML = `
            <div class="empty-state">
                <div class="empty-icon">
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" d="M20.25 8.511c.884.284 1.5 1.128 1.5 2.097v4.286c0 1.136-.847 2.1-1.98 2.193-.34.027-.68.052-1.02.072v3.091l-3-3c-1.354 0-2.694-.055-4.02-.163a2.115 2.115 0 01-.825-.242m9.345-8.334a2.126 2.126 0 00-.476-.095 48.64 48.64 0 00-8.048 0c-1.131.094-1.976 1.057-1.976 2.192v4.286c0 .837.46 1.58 1.155 1.951m9.345-8.334V6.637c0-1.621-1.152-3.026-2.76-3.235A48.455 48.455 0 0011.25 3c-2.115 0-4.198.137-6.24.402-1.608.209-2.76 1.614-2.76 3.235v6.226c0 1.621 1.152 3.026 2.76 3.235.577.075 1.157.14 1.74.194V21l4.155-4.155"/>
                    </svg>
                </div>
                <div class="empty-title">Пока нет чатов</div>
                <div class="empty-text">Создайте первый чат или дождитесь других</div>
            </div>
        `;
        return;
    }

    container.innerHTML = list
        .map(
            (u) => `
                    <div class="room-card">
                        <div class="room-avatar">
                            <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor">
                                <path stroke-linecap="round" stroke-linejoin="round" d="M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H8.25m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0H12m4.125 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm0 0h-.375M21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.979 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z"/>
                            </svg>
                        </div>
                        <div class="room-info">
                            <div class="room-name">${escapeHtml(u.title)}</div>
                            <div class="room-meta">
                                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2" stroke="currentColor">
                                    <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 11-7.5 0 3.75 3.75 0 017.5 0zM4.501 20.118a7.5 7.5 0 0114.998 0A17.933 17.933 0 0112 21.75c-2.676 0-5.216-.584-7.499-1.632z"/>
                                </svg>
                                ${escapeHtml(u.nickname)}
                            </div>
                        </div>
                        <button class="btn-connect" data-target="${u.client_id}">
                            <span>Войти</span>
                            <div class="btn-loader" style="display:none"></div>
                        </button>
                    </div>
                    `,
        )
        .join("");

    document.querySelectorAll(".btn-connect").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            const targetId = e.currentTarget.dataset.target;
            connectToUser(targetId, btn);
        });
    });
}

function filterUsers() {
    const term = document.getElementById("search").value.toLowerCase();
    document.querySelectorAll(".room-card").forEach((card) => {
        card.style.display = card.textContent.toLowerCase().includes(term)
            ? ""
            : "none";
    });
}

window.setNickname = setNickname;
window.createRoom = createRoom;
window.connectToUser = connectToUser;
window.filterUsers = filterUsers;
window.renderUsers = renderUsers;

window.exitChat = function () {
    document.querySelectorAll(".btn-connect.loading").forEach((btn) => {
        btn.classList.remove("loading");
        btn.querySelector(".btn-loader").style.display = "none";
    });

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "peer_disconnected" }));
    }
    if (typeof window._exitChatInternal === "function") {
        window._exitChatInternal();
    }
};

window.parseLinks = function (text) {
    const urlRegex =
        /(\bhttps?:\/\/[-A-Z0-9+&@#\/%?=~_|!:,.;]*[-A-Z0-9+&@#\/%=~_|])/gi;
    return text.replace(urlRegex, (url) => {
        try {
            const safeUrl = escapeHtml(url);
            return `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer">${safeUrl}</a>`;
        } catch (e) {
            return escapeHtml(url);
        }
    });
};

// Функция для преобразования HTML-сущностей обратно в текст (для имён файлов)
function decodeHtmlEntities(str) {
    const textarea = document.createElement("textarea");
    textarea.innerHTML = str;
    return textarea.value;
}

// P2P Services Menu
const services = [
    // {
    //     id: "file-transfer",
    //     name: "P2P Передача файлов",
    //     description: "Мгновенная передача файлов напрямую между устройствами",
    //     icon: "file",
    //     url: "/file",
    //     color: "#3b82f6",
    // },
    {
        id: "chat",
        name: "P2P Чат",
        description: "Шифрованный чат с конфиденциальностью по умолчанию",
        icon: "chat",
        url: "/chat",
        color: "#22c55e",
        active: true,
    },
];

function initServicesMenu() {
    const menuBtn = document.getElementById("servicesMenuBtn");
    const closeBtn = document.getElementById("servicesCloseBtn");
    const dropdown = document.getElementById("servicesDropdown");
    const overlay = document.createElement("div");
    overlay.className = "services-overlay";
    document.body.appendChild(overlay);

    const servicesList = document.getElementById("servicesList");
    servicesList.innerHTML = services
        .map(
            (service) => `
        <a href="${service.url}" class="service-item ${service.active ? "active" : ""}" data-id="${service.id}">
            <div class="service-icon ${service.icon}">
                ${
                    service.icon === "file"
                        ? `
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                `
                        : `
                <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
                `
                }
            </div>
            <div class="service-info">
                <h4>${service.name}</h4>
                <p>${service.description}</p>
            </div>
        </a>
    `,
        )
        .join("");

    menuBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        dropdown.classList.add("open");
        overlay.classList.add("active");
        document.body.style.overflow = "hidden";
    });

    const closeMenu = () => {
        dropdown.classList.remove("open");
        overlay.classList.remove("active");
        document.body.style.overflow = "";
    };

    closeBtn.addEventListener("click", closeMenu);
    overlay.addEventListener("click", closeMenu);

    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape" && dropdown.classList.contains("open")) {
            closeMenu();
        }
    });

    document.querySelectorAll(".service-item").forEach((item) => {
        item.addEventListener("click", (e) => {
            if (item.classList.contains("active")) {
                e.preventDefault();
                closeMenu();
                return;
            }
            if (item.href.startsWith("http")) {
                e.preventDefault();
                window.open(item.href, "_blank");
                closeMenu();
            }
        });
    });
}

// ==================== ATTACHMENT BUTTON & CONTEXT MENU ====================
document.addEventListener("DOMContentLoaded", () => {
    const attachBtn = document.getElementById("attachBtn");
    const fileInput = document.getElementById("fileInput");

    if (attachBtn && fileInput) {
        attachBtn.addEventListener("click", () => {
            mobileFilePickerOpen = true;
            document.body.classList.add("file-picker-open");
            fileInput.click();
        });

        fileInput.addEventListener("change", (e) => {
            mobileFilePickerOpen = false;
            document.body.classList.remove("file-picker-open");
            if (e.target.files && e.target.files.length) {
                const files = Array.from(e.target.files);
                const tooLarge = files.find(
                    (file) => file.size > 1024 * 1024 * 1024 * 100,
                );
                if (tooLarge) {
                    alert(
                        `Файл ${tooLarge.name} слишком большой! Максимальный размер: 100 ГБ`,
                    );
                    fileInput.value = "";
                    return;
                }
                if (typeof window.enqueueFiles === "function") {
                    window.enqueueFiles(files);
                } else if (typeof window.sendFile === "function") {
                    files.forEach((file) => window.sendFile(file));
                } else {
                    alert("Сначала установите соединение с собеседником");
                }
                fileInput.value = "";
            }
        });

        window.addEventListener("focus", () => {
            if (!mobileFilePickerOpen) return;
            mobileFilePickerOpen = false;
            document.body.classList.remove("file-picker-open");
        });
    }

    initServicesMenu();
    connectWebSocket();
});

// Контекстное меню инициализируем после полной загрузки всех скриптов
window.addEventListener("load", () => {
    const menuIcon = (svg) => `<span class="context-menu-icon">${svg}</span>`;
    const replyIcon = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" version="1.1" viewBox="0 0 32 32"><path fill="currentColor" d="M29.934,24.707c-0.076-0.892-0.193-1.784-0.353-2.666c-0.162-0.906-0.417-1.778-0.787-2.62c-0.337-0.771-0.75-1.501-1.155-2.238c-0.197-0.356-0.405-0.707-0.615-1.057c-0.249-0.411-0.538-0.793-0.837-1.169c-0.506-0.637-1.093-1.221-1.687-1.776c-0.666-0.623-1.352-1.225-2.142-1.689c-0.736-0.43-1.484-0.853-2.269-1.188c-0.427-0.183-0.857-0.37-1.305-0.5c-0.425-0.123-0.853-0.226-1.289-0.3c-0.442-0.076-0.882-0.152-1.32-0.25c-0.232-0.052-0.461-0.113-0.691-0.171c-0.13-1.452-0.15-2.915-0.25-4.371c-0.002-0.029-0.016-0.055-0.02-0.083c0.03-0.234-0.03-0.477-0.211-0.658c-0.148-0.148-0.353-0.234-0.563-0.234c-0.195,0-0.434,0.078-0.565,0.234c-0.249,0.3-0.528,0.573-0.799,0.855c-0.249,0.261-0.504,0.516-0.76,0.773c-0.551,0.553-1.133,1.077-1.694,1.622C10.058,7.768,9.509,8.327,8.94,8.868c-0.565,0.537-1.139,1.065-1.692,1.615c-0.795,0.785-1.525,1.632-2.327,2.411c-0.775,0.75-1.505,1.544-2.221,2.353c-0.169,0.03-0.332,0.093-0.456,0.216c-0.331,0.331-0.317,0.861,0,1.194c0.647,0.678,1.299,1.348,1.946,2.026c0.575,0.602,1.145,1.208,1.712,1.817c0.584,0.625,1.196,1.221,1.807,1.821c0.59,0.576,1.178,1.157,1.768,1.733c0.711,0.693,1.441,1.362,2.177,2.026c0.352,0.317,0.717,0.623,1.075,0.935c0.337,0.291,0.678,0.587,0.983,0.912c0.246,0.319,0.675,0.445,1.045,0.231c0.074-0.043,0.135-0.104,0.191-0.169c0.257-0.144,0.449-0.407,0.435-0.715c-0.035-0.837-0.027-1.677-0.033-2.516c-0.005-0.781-0.006-1.561-0.011-2.34c1.341-0.068,2.68-0.152,4.019,0.005c0.58,0.096,1.157,0.218,1.722,0.375c0.552,0.154,1.083,0.361,1.612,0.582c0.463,0.202,0.921,0.42,1.359,0.675c0.483,0.283,0.942,0.599,1.39,0.937c0.437,0.342,0.871,0.69,1.305,1.036c0.439,0.354,0.885,0.716,1.254,1.143c0.004,0.006,0.008,0.012,0.012,0.018c0.126,0.179,0.271,0.309,0.478,0.377c0.155,0.202,0.395,0.344,0.646,0.344c0.464,0,0.785-0.37,0.812-0.812c0.025-0.411,0.062-0.816,0.051-1.229C29.988,25.478,29.965,25.092,29.934,24.707z"/></svg>`;
    const deleteIcon = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" fill="currentColor" viewBox="0 0 24 24"><path d="M22,5H17V2a1,1,0,0,0-1-1H8A1,1,0,0,0,7,2V5H2A1,1,0,0,0,2,7H3.117L5.008,22.124A1,1,0,0,0,6,23H18a1,1,0,0,0,.992-.876L20.883,7H22a1,1,0,0,0,0-2ZM9,3h6V5H9Zm8.117,18H6.883L5.133,7H18.867Z"/></svg>`;
    const editIcon = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M18 9.99982L14 5.99982M2.5 21.4998L5.88437 21.1238C6.29786 21.0778 6.5046 21.0549 6.69785 20.9923C6.86929 20.9368 7.03245 20.8584 7.18289 20.7592C7.35245 20.6474 7.49955 20.5003 7.79373 20.2061L21 6.99982C22.1046 5.89525 22.1046 4.10438 21 2.99981C19.8955 1.89525 18.1046 1.89524 17 2.99981L3.79373 16.2061C3.49955 16.5003 3.35246 16.6474 3.24064 16.8169C3.14143 16.9674 3.06301 17.1305 3.00751 17.302C2.94496 17.4952 2.92198 17.702 2.87604 18.1155L2.5 21.4998Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    const copyIcon = `<svg fill="none" width="16" height="16" viewBox="0 0 36 36" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M29.5,7h-19A1.5,1.5,0,0,0,9,8.5v24A1.5,1.5,0,0,0,10.5,34h19A1.5,1.5,0,0,0,31,32.5V8.5A1.5,1.5,0,0,0,29.5,7ZM29,32H11V9H29Z"/><path fill="currentColor" d="M26,3.5A1.5,1.5,0,0,0,24.5,2H5.5A1.5,1.5,0,0,0,4,3.5v24A1.5,1.5,0,0,0,5.5,29H6V4H26Z"/></svg>`;
    const saveIcon = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M21 21H3M18 11L12 17M12 17L6 11M12 17V3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

    const reactionBar = document.createElement("div");
    reactionBar.className = "context-reaction-bar";
    document.body.appendChild(reactionBar);
    const contextMenu = document.createElement("div");
    contextMenu.className = "context-menu";
    contextMenu.innerHTML = `
        <div class="context-menu-item" id="replyMenuItem">${menuIcon(replyIcon)}Ответить</div>
        <div class="context-menu-item" id="editMenuItem">${menuIcon(editIcon)}Редактировать</div>
        <div class="context-menu-item" id="deleteMenuItem">${menuIcon(deleteIcon)}Удалить</div>
        <div class="context-menu-item" id="copyMenuItem">${menuIcon(copyIcon)}Копировать текст</div>
        <div class="context-menu-item" id="saveImageMenuItem" style="display: none;">${menuIcon(saveIcon)}Сохранить изображение</div>
        <div class="context-menu-item" id="saveFileMenuItem" style="display: none;">${menuIcon(saveIcon)}Сохранить файл</div>`;
    document.body.appendChild(contextMenu);

    let selectedMessage = null;
    const hideMenus = () => {
        contextMenu.style.display = "none";
        reactionBar.style.display = "none";
        reactionBar.classList.remove("expanded", "open-up");
        selectedMessage = null;
    };
    const EDGE_PADDING = 8;
    const MENU_GAP = 4;
    const MENU_BOTTOM_THRESHOLD = 220;
    const REACTION_EXPANDED_HEIGHT = 246;
    const clamp = (value, min, max) =>
        Math.min(Math.max(value, min), Math.max(min, max));
    const positionContextMenu = (messageEl) => {
        const rect = messageEl.getBoundingClientRect();
        contextMenu.style.visibility = "hidden";
        contextMenu.style.display = "flex";
        const menuWidth = contextMenu.offsetWidth || 220;
        const menuHeight = contextMenu.offsetHeight || 180;
        const spaceBelow = window.innerHeight - rect.bottom;
        const shouldOpenUp =
            spaceBelow < MENU_BOTTOM_THRESHOLD ||
            rect.bottom + MENU_GAP + menuHeight >
                window.innerHeight - EDGE_PADDING;
        const reactionRect =
            reactionBar.style.display === "none"
                ? null
                : reactionBar.getBoundingClientRect();
        const top = shouldOpenUp
            ? (reactionRect?.top || rect.top) - menuHeight - MENU_GAP
            : rect.bottom + MENU_GAP;
        contextMenu.style.left = `${clamp(
            rect.left,
            EDGE_PADDING,
            window.innerWidth - menuWidth - EDGE_PADDING,
        )}px`;
        contextMenu.style.top = `${clamp(
            top,
            EDGE_PADDING,
            window.innerHeight - menuHeight - EDGE_PADDING,
        )}px`;
        contextMenu.style.visibility = "visible";
    };
    const showReactions = (messageEl) => {
        const base = ["🔥", "👍", "❤️", "😂", "😮", "😢"];
        const all =
            typeof emojiData === "object"
                ? Object.values(emojiData).flat()
                : base;
        reactionBar.innerHTML =
            base
                .map(
                    (emoji) =>
                        `<button type="button" class="reaction-option" data-emoji="${emoji}">${emoji}</button>`,
                )
                .join("") +
            `<button type="button" class="reaction-expand" aria-label="Все реакции"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 1024 1024" fill="currentColor" class="icon" version="1.1"><path d="M478.312 644.16c24.38 26.901 64.507 26.538 88.507-0.89l270.57-309.222c7.758-8.867 6.86-22.344-2.008-30.103-8.866-7.759-22.344-6.86-30.103 2.007L534.71 615.173c-7.202 8.231-17.541 8.325-24.782 0.335L229.14 305.674c-7.912-8.73-21.403-9.394-30.133-1.482s-9.394 21.403-1.482 30.134l280.786 309.833z" fill=""/></svg></button><div class="reaction-all">${all.map((emoji) => `<button type="button" class="reaction-option" data-emoji="${emoji}">${emoji}</button>`).join("")}</div>`;
        reactionBar.querySelector(".reaction-expand").onclick = (event) => {
            event.stopPropagation();
            reactionBar.classList.toggle("expanded");
        };
        reactionBar.querySelectorAll(".reaction-option").forEach(
            (button) =>
                (button.onclick = (event) => {
                    event.stopPropagation();
                    if (selectedMessage && window.toggleMessageReaction)
                        window.toggleMessageReaction(
                            selectedMessage.dataset.messageId,
                            button.dataset.emoji,
                        );
                    hideMenus();
                }),
        );
        reactionBar.classList.remove("expanded", "open-up");
        const rect = messageEl.getBoundingClientRect();
        const spaceBelow = window.innerHeight - rect.bottom;
        if (spaceBelow < REACTION_EXPANDED_HEIGHT) {
            reactionBar.classList.add("open-up");
        }
        reactionBar.style.left = `${clamp(
            rect.left,
            EDGE_PADDING,
            window.innerWidth - 320,
        )}px`;
        reactionBar.style.top = `${Math.max(EDGE_PADDING, rect.top - 54)}px`;
        reactionBar.style.display = "flex";
    };

    document.addEventListener("contextmenu", (e) => {
        const messageEl = e.target.closest(".message");
        if (
            !messageEl ||
            messageEl.classList.contains("system-message") ||
            messageEl.classList.contains("deleted")
        ) {
            hideMenus();
            return;
        }
        e.preventDefault();
        selectedMessage = messageEl;
        const isFile = messageEl.classList.contains("file");
        const isImage = messageEl.classList.contains("image");
        const isOwnMessage = messageEl.classList.contains("me");
        document.getElementById("replyMenuItem").style.display = "flex";
        const canReply = true;
        const canReact = true;
        document.getElementById("replyMenuItem").style.display = canReply
            ? "flex"
            : "none";
        document.getElementById("copyMenuItem").style.display =
            isFile || isImage ? "none" : "flex";
        document.getElementById("editMenuItem").style.display =
            isOwnMessage && !isFile && !isImage ? "flex" : "none";
        document.getElementById("deleteMenuItem").style.display =
            isOwnMessage && !isFile && !isImage ? "flex" : "none";
        document.getElementById("saveImageMenuItem").style.display = isImage
            ? "flex"
            : "none";
        document.getElementById("saveFileMenuItem").style.display = isFile
            ? "flex"
            : "none";
        if (canReact) {
            showReactions(messageEl);
        } else {
            reactionBar.style.display = "none";
            reactionBar.classList.remove("expanded", "open-up");
        }
        positionContextMenu(messageEl);
    });

    document.addEventListener("click", (e) => {
        if (!contextMenu.contains(e.target) && !reactionBar.contains(e.target))
            hideMenus();
    });
    document.getElementById("replyMenuItem").onclick = () => {
        const canReply = selectedMessage && !selectedMessage.classList.contains("deleted");
        if (canReply && window.replyToMessage)
            window.replyToMessage(selectedMessage.dataset.messageId);
        hideMenus();
    };
    document.getElementById("editMenuItem").onclick = () => {
        if (selectedMessage && window.editMessage)
            window.editMessage(selectedMessage.dataset.messageId);
        hideMenus();
    };
    document.getElementById("deleteMenuItem").onclick = () => {
        if (selectedMessage && window.deleteMessage)
            window.deleteMessage(selectedMessage.dataset.messageId);
        hideMenus();
    };
    document.getElementById("copyMenuItem").onclick = () => {
        const textEl = selectedMessage?.querySelector(".message-content");
        if (textEl)
            navigator.clipboard
                .writeText(textEl.textContent)
                .then(
                    () =>
                        typeof addSystemMessage === "function" &&
                        addSystemMessage("✅ Текст скопирован"),
                );
        hideMenus();
    };
    document.getElementById("saveImageMenuItem").onclick = () => {
        if (selectedMessage?.classList.contains("image")) {
            const a = document.createElement("a");
            a.href = selectedMessage.dataset.imageUrl;
            a.download = decodeHtmlEntities(
                selectedMessage.dataset.originalFilename || "image",
            );
            document.body.appendChild(a);
            a.click();
            a.remove();
            if (typeof addSystemMessage === "function")
                addSystemMessage("✅ Изображение сохранено");
        }
        hideMenus();
    };
    document.getElementById("saveFileMenuItem").onclick = () => {
        if (selectedMessage?.classList.contains("file")) {
            const a = document.createElement("a");
            a.href = selectedMessage.dataset.url;
            a.download = decodeHtmlEntities(
                selectedMessage.dataset.originalFilename || "file",
            );
            document.body.appendChild(a);
            a.click();
            a.remove();
            if (typeof addSystemMessage === "function")
                addSystemMessage("✅ Файл сохранён");
        }
        hideMenus();
    };
});
