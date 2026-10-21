# Klipin di Termux — Potong Langsung dari Link

Di Vercel, Klipin hanya bisa menganalisis: yang memotong video adalah browser
(ffmpeg.wasm), jadi file videonya harus didownload sendiri dulu. Di Termux,
Klipin jalan sebagai server lokal di HP kamu — ada ffmpeg + yt-dlp beneran
dan IP rumahan yang tidak diblokir YouTube — jadi alurnya lengkap:

**Tempel link YouTube → Analisis → pencet "Potong langsung" di kartu clip →
server lokal download videonya, memotong + menempel caption → Download klip jadi.**

## Install (sekali saja)

Di Termux:

```bash
pkg install -y git
git clone https://github.com/KimTaehyung666/Klipin.git ~/klipin
bash ~/klipin/termux-install.sh
```

Installer memasang Python, ffmpeg, font DejaVu (buat caption), dan yt-dlp.
Lalu isi API key di `~/klipin/.env` (sama seperti di Vercel):

```
GROQ_API_KEY=...
SUPADATA_API_KEY=...
```

## Menjalankan

```bash
bash ~/klipin/termux-run.sh
```

Buka **http://localhost:5003** di browser HP. Kalau chip **POTONG LANGSUNG**
di kanan atas menyala, mode lokal aktif: setelah analisis, setiap kartu clip
punya tombol **Potong langsung**. Opsi vertikal 9:16 dan gaya caption
mengikuti pilihan "Gaya caption" di Studio Potong.

Catatan:

- Video sumber didownload maksimal 720p dan disimpan sebagai cache — clip
  kedua dari video yang sama tidak download ulang.
- Potong langsung hanya untuk hasil analisis dari **link YouTube**. Untuk
  file lokal tetap pakai Studio Potong seperti biasa.
- Biarkan Termux terbuka selama server dipakai (wake-lock sudah dipasang
  di script run).
