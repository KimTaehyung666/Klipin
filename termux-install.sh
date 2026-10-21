#!/data/data/com.termux/files/usr/bin/bash
# Installer Klipin untuk Termux — SATU KALI jalan.
# Setelah ini, jalankan Klipin kapan pun dengan: bash ~/klipin/termux-run.sh
# Mode Termux membuka fitur POTONG LANGSUNG: tempel link YouTube -> analisis
# -> server lokal (HP kamu sendiri) yang download + memotong videonya.
set -e

echo "== Klipin Termux Installer =="
pkg update -y
pkg install -y python ffmpeg git ttf-dejavu

if [ -d "$HOME/klipin/.git" ]; then
  echo "-- Update Klipin yang sudah ada..."
  cd "$HOME/klipin" && git pull
else
  echo "-- Download Klipin..."
  git clone https://github.com/KimTaehyung666/Klipin.git "$HOME/klipin"
  cd "$HOME/klipin"
fi

pip install --upgrade pip
pip install -r requirements.txt yt-dlp

if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo "!! PENTING: isi API key dulu di file .env (GROQ_API_KEY & SUPADATA_API_KEY):"
  echo "   nano ~/klipin/.env"
fi

chmod +x termux-run.sh termux-install.sh
echo ""
echo "Selesai! Jalankan Klipin dengan:"
echo "  bash ~/klipin/termux-run.sh"
echo "Lalu buka http://localhost:5003 di browser HP kamu."
