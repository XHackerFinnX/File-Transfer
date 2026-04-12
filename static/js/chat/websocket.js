let ws,
    myClientId = "";
let pendingRequest = false;

function connectWebSocket() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${location.host}/chat/ws`);
    window.ws = ws;

    ws.onmessage = async (e) => {
        const msg = JSON.parse(e.data);
        console.log("📨 Получено сообщение:", msg.type, msg.data);

        if (msg.type === "init") {
            myClientId = msg.data.client_id;
        } else if (msg.type === "users") {
            renderUsers(msg.data);
        } else if (msg.type === "incoming_request") {
            document.querySelectorAll(".btn-connect.loading").forEach((btn) => {
                btn.classList.remove("loading");
                btn.querySelector(".btn-loader").style.display = "none";
            });
            showConnectionRequestDialog(msg.data.from_nickname, msg.data.from);
        } else if (msg.type === "room_created") {
            console.log("✅ Создатель → переключаемся в чат");
            document.getElementById("lobby").style.display = "none";
            document.getElementById("chat").style.display = "flex";
            document.getElementById("chatTitle").textContent =
                msg.data.title || "Мой чат";
            const statusEl = document.getElementById("chatPeerStatusText");
            if (statusEl)
                statusEl.textContent =
                    "Ожидаем подключения другого пользователя...";
        } else if (msg.type === "start_connection") {
            document.querySelectorAll(".btn-connect.loading").forEach((btn) => {
                btn.classList.remove("loading");
                btn.querySelector(".btn-loader").style.display = "none";
            });
            console.log("🚀 start_connection получен! Переключаем в чат");
            document.getElementById("lobby").style.display = "none";
            document.getElementById("chat").style.display = "flex";
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
            alert(`Ошибка: ${msg.data.reason}`);
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
            ].includes(msg.type)
        ) {
            if (typeof window.handleWebRTCMessage === "function") {
                window.handleWebRTCMessage(msg);
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
            <div class="dialog-text">Пользователь <strong>${escapeHtml(fromNickname)}</strong> хочет подключиться к вашему чату.</div>
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
                padding: 24px;
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
                margin-bottom: 24px;
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
            fileInput.click();
        });

        fileInput.addEventListener("change", (e) => {
            if (e.target.files && e.target.files[0]) {
                const file = e.target.files[0];
                if (file.size > 1024 * 1024 * 1024) {
                    alert("Файл слишком большой! Максимальный размер: 1 ГБ");
                    fileInput.value = "";
                    return;
                }
                if (typeof window.sendFile === "function") {
                    window.sendFile(file);
                } else {
                    alert("Сначала установите соединение с собеседником");
                }
                fileInput.value = "";
            }
        });
    }

    initServicesMenu();
    connectWebSocket();
});

// Контекстное меню инициализируем после полной загрузки всех скриптов
window.addEventListener("load", () => {
    const contextMenu = document.createElement("div");
    contextMenu.className = "context-menu";
    contextMenu.innerHTML = `
        <div class="context-menu-item" id="copyMenuItem">             
            <svg fill="none" width="16px" height="16px" viewBox="0 0 36 36" version="1.1"  preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink">
                <title>copy-line</title>
                <path d="M29.5,7h-19A1.5,1.5,0,0,0,9,8.5v24A1.5,1.5,0,0,0,10.5,34h19A1.5,1.5,0,0,0,31,32.5V8.5A1.5,1.5,0,0,0,29.5,7ZM29,32H11V9H29Z" class="clr-i-outline clr-i-outline-path-1"></path><path d="M26,3.5A1.5,1.5,0,0,0,24.5,2H5.5A1.5,1.5,0,0,0,4,3.5v24A1.5,1.5,0,0,0,5.5,29H6V4H26Z" class="clr-i-outline clr-i-outline-path-2"></path>
            </svg>         
            Копировать текст
        </div>
        <div class="context-menu-item" id="saveImageMenuItem" style="display: none;">
            <svg version="1.1" id="Icons" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" 
                width="16px" height="16px" viewBox="0 0 32 32" xml:space="preserve">
            <style type="text/css">
                .st0{fill:none;stroke:#ffffff;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;stroke-miterlimit:10;}
                .st1{fill:none;stroke:#ffffff;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;}
                .st2{fill:none;stroke:#ffffff;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:6,6;}
                .st3{fill:none;stroke:#ffffff;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:4,4;}
                .st4{fill:none;stroke:#ffffff;stroke-width:2;stroke-linecap:round;}
                .st5{fill:none;stroke:#ffffff;stroke-width:2;stroke-linecap:round;stroke-dasharray:3.1081,3.1081;}
                
                    .st6{fill:none;stroke:#ffffff;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;stroke-miterlimit:10;stroke-dasharray:4,3;}
            </style>
            <path class="st0" d="M27,16V8.8c1.2-0.4,2-1.5,2-2.8c0-1.7-1.3-3-3-3c-1.3,0-2.4,0.8-2.8,2H8.8C8.4,3.8,7.3,3,6,3C4.3,3,3,4.3,3,6
                c0,1.3,0.8,2.4,2,2.8v14.4c-1.2,0.4-2,1.5-2,2.8c0,1.7,1.3,3,3,3c1.3,0,2.4-0.8,2.8-2H18"/>
            <polyline class="st0" points="16,23 9,23 9,9 23,9 23,15 "/>
            <circle class="st0" cx="13" cy="13" r="1"/>
            <polyline class="st0" points="9,20 16,16 17,17 "/>
            <circle class="st0" cx="23" cy="22" r="7"/>
            <line class="st0" x1="23" y1="15" x2="23" y2="25"/>
            <polyline class="st0" points="19,21 23,25 27,21 "/>
            </svg>
            Сохранить изображение
        </div>
        <div class="context-menu-item" id="saveFileMenuItem" style="display: none;">                 
            <svg width="16px" height="16px" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M21 21H3M18 11L12 17M12 17L6 11M12 17V3" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
            </svg>        
            Сохранить файл
        </div>
    `;
    document.body.appendChild(contextMenu);

    let selectedMessage = null;

    document.addEventListener("contextmenu", (e) => {
        const messageEl = e.target.closest(".message");
        if (messageEl && !messageEl.classList.contains("system-message")) {
            e.preventDefault();
            selectedMessage = messageEl;

            const saveImageMenuItem =
                document.getElementById("saveImageMenuItem");
            saveImageMenuItem.style.display = messageEl.classList.contains(
                "image",
            )
                ? "flex"
                : "none";

            const saveFileMenuItem =
                document.getElementById("saveFileMenuItem");
            saveFileMenuItem.style.display = messageEl.classList.contains(
                "file",
            )
                ? "flex"
                : "none";

            const copyMenuItem = document.getElementById("copyMenuItem");
            copyMenuItem.style.display =
                messageEl.classList.contains("file") ||
                messageEl.classList.contains("image")
                    ? "none"
                    : "flex";

            const rect = messageEl.getBoundingClientRect();
            contextMenu.style.left = `${Math.min(rect.left, window.innerWidth - 200)}px`;
            contextMenu.style.top = `${Math.min(rect.bottom + 4, window.innerHeight - 150)}px`;
            contextMenu.style.display = "flex";
        } else {
            contextMenu.style.display = "none";
            selectedMessage = null;
        }
    });

    document.addEventListener("click", (e) => {
        if (!contextMenu.contains(e.target)) {
            contextMenu.style.display = "none";
            selectedMessage = null;
        }
    });

    document.getElementById("copyMenuItem").addEventListener("click", () => {
        if (selectedMessage) {
            const textEl = selectedMessage.querySelector(".message-content");
            if (textEl) {
                navigator.clipboard.writeText(textEl.textContent).then(() => {
                    if (typeof addSystemMessage === "function")
                        addSystemMessage("✅ Текст скопирован");
                });
            }
        }
        contextMenu.style.display = "none";
    });

    document
        .getElementById("saveImageMenuItem")
        .addEventListener("click", () => {
            if (
                selectedMessage &&
                selectedMessage.classList.contains("image")
            ) {
                const url = selectedMessage.dataset.imageUrl;
                let fileName =
                    selectedMessage.dataset.originalFilename || "image";
                fileName = decodeHtmlEntities(fileName);
                const a = document.createElement("a");
                a.href = url;
                a.download = fileName;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                if (typeof addSystemMessage === "function")
                    addSystemMessage("✅ Изображение сохранено");
            }
            contextMenu.style.display = "none";
        });

    document
        .getElementById("saveFileMenuItem")
        .addEventListener("click", () => {
            if (selectedMessage && selectedMessage.classList.contains("file")) {
                const url = selectedMessage.dataset.url;
                let fileName =
                    selectedMessage.dataset.originalFilename || "file";
                fileName = decodeHtmlEntities(fileName);
                const a = document.createElement("a");
                a.href = url;
                a.download = fileName;
                document.body.appendChild(a);
                a.click();
                document.body.removeChild(a);
                if (typeof addSystemMessage === "function")
                    addSystemMessage("✅ Файл сохранён");
            }
            contextMenu.style.display = "none";
        });
});
