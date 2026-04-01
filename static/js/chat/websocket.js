let ws,
    myClientId = "";
let pendingRequest = false;

function connectWebSocket() {
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${protocol}//${location.host}/chat/ws`);
    window.ws = ws; // для доступа из webrtc.js

    ws.onmessage = async (e) => {
        const msg = JSON.parse(e.data);
        console.log("📨 Получено сообщение:", msg.type, msg.data);

        if (msg.type === "init") {
            myClientId = msg.data.client_id;
        } else if (msg.type === "users") {
            renderUsers(msg.data);
        } else if (msg.type === "incoming_request") {
            // Сбрасываем спиннер при получении входящего запроса (чтобы не зависал)
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
            const statusEl = document.getElementById("statusChat");
            if (statusEl)
                statusEl.textContent =
                    "Ожидаем подключения другого пользователя...";
        } else if (msg.type === "start_connection") {
            // Сбрасываем спиннер при успешном подключении
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
            // Сбрасываем спиннер при отказе
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
    // Создаём кастомный диалог вместо нативного confirm()
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

    // Закрытие кликом вне диалога — НЕ отклоняем запрос
    dialog.onclick = (e) => {
        if (e.target === dialog) {
            document.body.removeChild(dialog);
        }
    };

    // Стили для диалога (встраиваются один раз)
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

    // Показываем спиннер на кнопке
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

    // Сбрасываем состояние через 10 секунд (таймаут)
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

    // Добавляем обработчики для кнопок после рендера
    document.querySelectorAll(".btn-connect").forEach((btn) => {
        btn.addEventListener("click", (e) => {
            const targetId = e.currentTarget.dataset.target;
            connectToUser(targetId, btn); // Передаём кнопку в функцию
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

// Глобальные функции
window.setNickname = setNickname;
window.createRoom = createRoom;
window.connectToUser = connectToUser;
window.filterUsers = filterUsers;
window.renderUsers = renderUsers;
window.exitChat = function () {
    // Сбрасываем все спиннеры
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

// Инициализация при загрузке DOM
document.addEventListener("DOMContentLoaded", () => {
    // Навигационная кнопка
    const navBtn = document.getElementById("navBtn");
    if (navBtn) {
        navBtn.addEventListener("click", () => {
            window.open("https://2p2p.ru/file", "_blank");
        });
    }

    // Подключаемся к WebSocket
    connectWebSocket();
});
