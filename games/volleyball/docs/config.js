// Игра и WebSocket работают на том же домене, что и FastAPI.
window.VOLLEY_CONFIG = Object.freeze({ serverUrl: '', websocketPath: '/volleyball/ws' });
