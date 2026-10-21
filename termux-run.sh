#!/data/data/com.termux/files/usr/bin/bash
# Jalankan Klipin di Termux (server lokal di HP kamu).
cd "$(dirname "$0")"
termux-wake-lock 2>/dev/null || true
echo "Klipin jalan di http://localhost:5003 — buka di browser HP kamu."
echo "Chip 'POTONG LANGSUNG' di kanan atas = mode lokal aktif."
PORT=5003 python app.py
