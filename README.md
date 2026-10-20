# Klipin — Tools Clipper AI

Tempel link YouTube → AI (Groq) membaca transcript, milihin momen terbaik, dan bikin hook + hashtag. Terus **potong clip-nya langsung di browser** (ffmpeg.wasm): file video diproses di perangkat user, bukan di server — makanya tool ini bisa jalan full di **Vercel** (serverless).

## Cara kerja

- **Analisis (server):** transcript diambil berlapis — library langsung → TranscriptAPI → Supadata → tempelan manual user. AI Groq milih momen + hook per gaya konten, ada target durasi clip.
- **Studio Potong (browser):** user memilih file video (mis. hasil download dari YouTube), set timestamp (otomatis terisi kalau klik "Potong" di hasil analisis), pilih vertikal 9:16 / caption, lalu ffmpeg.wasm memotongnya jadi MP4 siap download. File tidak pernah diupload ke server.
- **Generator Hook:** dari topik/cuplikan apa pun, AI bikin 5–15 variasi hook beda sudut pandang (penasaran, angka, kesalahan umum, dll), siap salin.
- **Riwayat:** tersimpan di localStorage browser user.

## Deploy ke Vercel

1. Push repo ini ke GitHub, lalu di Vercel: **Add New → Project → import repo ini** (sudah ada `vercel.json`).
2. Di **Settings → Environment Variables**, isi:
   - `GROQ_API_KEY` — wajib (dari console.groq.com)
   - `SUPADATA_API_KEY` **atau** `TRANSCRIPT_API_KEY` — biar transcript otomatis jalan dari IP Vercel (YouTube memblokir IP cloud). Gratis, tanpa kartu: supadata.ai (100 kredit/bulan) / transcriptapi.com (100 kredit awal).
3. Deploy. Tanpa key transcript pun app tetap bisa dipakai lewat tempel transcript manual.

## Jalanin lokal

```bash
pip install -r requirements.txt
cp .env.example .env   # isi GROQ_API_KEY
python app.py          # http://localhost:5003
```

## Catatan

- Model Groq default `openai/gpt-oss-120b` (bisa dioverride via `GROQ_MODEL`).
- Motong di browser HP kelas bawah bisa lambat (1–5 menit untuk clip pendek) dan file besar (>300MB) bisa gagal — itu batas wajar ffmpeg.wasm, bukan bug.
- Jangan commit `.env`.
