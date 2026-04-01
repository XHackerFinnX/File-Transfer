// Экранирование HTML для защиты от XSS
function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// Автоподстройка высоты textarea
function adjustTextareaHeight(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
}

// Глобальный экспорт
window.escapeHtml = escapeHtml;
window.adjustTextareaHeight = adjustTextareaHeight;
