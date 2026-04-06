// Экранирование HTML для защиты от XSS
function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// Экранирование для атрибутов HTML
function escapeAttr(str) {
    if (!str) return "";
    return str.replace(/[&"']/g, function (m) {
        if (m === "&") return "&amp;";
        if (m === '"') return "&quot;";
        if (m === "'") return "&#39;";
        return m;
    });
}

// Автоподстройка высоты textarea
function adjustTextareaHeight(textarea) {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 120) + "px";
}

// Глобальный экспорт
window.escapeHtml = escapeHtml;
window.escapeAttr = escapeAttr;
window.adjustTextareaHeight = adjustTextareaHeight;
