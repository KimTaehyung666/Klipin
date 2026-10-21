import os, json, re
from flask import Flask, render_template, request, jsonify, send_file
from dotenv import load_dotenv
import requests
from urllib.parse import urlparse, parse_qs

load_dotenv()

app = Flask(__name__)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
# Model lama "llama-3.3-70b-versatile" sudah dimatikan Groq per 16 Agu 2026.
# Pengganti resmi dari Groq: openai/gpt-oss-120b. Bisa dioverride via env GROQ_MODEL.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Fallback transcript pihak ketiga. YouTube memblokir transcript dari IP cloud
# (Vercel/Render/dll), jadi di deploy server, transcript diambil lewat API ini.
TRANSCRIPT_API_KEY = os.getenv("TRANSCRIPT_API_KEY")
SUPADATA_API_KEY = os.getenv("SUPADATA_API_KEY")

def call_groq(prompt, max_tokens=2500):
    if not GROQ_API_KEY:
        raise Exception("GROQ_API_KEY belum diset. Isi file .env (lokal) atau Environment Variable (deploy) dulu ya.")
    try:
        response = requests.post(
            GROQ_URL,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
            },
            timeout=60
        )
    except requests.Timeout:
        raise
    except requests.RequestException as e:
        raise Exception(f"Gagal menghubungi Groq: {e}")

    try:
        result = response.json()
    except ValueError:
        raise Exception(f"Groq membalas format aneh (HTTP {response.status_code}), coba lagi.")

    if response.status_code == 401:
        raise Exception("API key Groq tidak valid / sudah mati. Cek GROQ_API_KEY kamu.")
    if response.status_code == 429:
        raise Exception("Kena rate limit Groq, tunggu sebentar lalu coba lagi.")
    if "error" in result:
        raise Exception(result["error"].get("message", "Groq error"))
    if response.status_code >= 400:
        raise Exception(f"Groq error HTTP {response.status_code}")
    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise Exception("Response Groq tidak lengkap, coba lagi.")

def extract_video_id(url):
    parsed = urlparse(url)
    if parsed.hostname in ('youtu.be',):
        return parsed.path.strip('/').split('/')[0] or None
    if parsed.hostname in ('www.youtube.com', 'youtube.com', 'm.youtube.com', 'music.youtube.com'):
        if parsed.path == '/watch':
            return parse_qs(parsed.query).get('v', [None])[0]
        for prefix in ('/shorts/', '/live/', '/embed/'):
            if parsed.path.startswith(prefix):
                return parsed.path.split(prefix)[1].split('/')[0] or None
    return None

def _segment_to_dict(s):
    if isinstance(s, dict):
        return {"start": s.get('start', 0), "text": s.get('text', '')}
    return {"start": getattr(s, 'start', 0), "text": getattr(s, 'text', '')}

def fetch_transcript_with_timestamps(video_id):
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    raw = None
    last_error = None

    # Cara utama (API versi >= 1.0): fetch langsung, utamakan Indonesia lalu Inggris
    try:
        raw = api.fetch(video_id, languages=['id', 'en'])
    except Exception as e:
        last_error = e

    # Fallback: ambil dari daftar transcript yang tersedia
    if raw is None:
        try:
            tlist = api.list(video_id)
            transcript = None
            for lang in (['id'], ['en']):
                try:
                    transcript = tlist.find_transcript(lang)
                    break
                except Exception:
                    continue
            if transcript is None:
                # Terakhir: terima transcript bahasa apa pun yang pertama ketemu
                for t in tlist:
                    transcript = t
                    break
            if transcript is not None:
                raw = transcript.fetch()
        except Exception as e:
            last_error = e

    if raw is None:
        raise Exception(_short_transcript_error(last_error))

    return [_segment_to_dict(s) for s in raw]

def _short_transcript_error(e):
    """Ubah exception mentah youtube-transcript-api (yang teksnya bisa satu
    halaman penuh) jadi pesan pendek yang enak dibaca user."""
    if e is None:
        return "Transcript tidak tersedia."
    name = type(e).__name__
    mapping = {
        "IpBlocked": "YouTube memblokir IP server",
        "RequestBlocked": "YouTube memblokir IP server",
        "TranscriptsDisabled": "transcript dimatikan pemilik video",
        "NoTranscriptFound": "video ini tidak punya transcript",
        "NoTranscriptAvailable": "video ini tidak punya transcript",
        "VideoUnavailable": "video tidak tersedia / private",
        "VideoUnplayable": "video tidak bisa diputar",
    }
    if name in mapping:
        return mapping[name]
    return f"{name}: {str(e)[:120]}"

def _clean_segments(raw_items, start_key="start", scale=1.0):
    """Normalisasi daftar segmen dari API pihak ketiga ke format internal."""
    segments = []
    for item in raw_items or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text", "")).strip()
        try:
            start = float(item.get(start_key, 0)) / scale
        except (TypeError, ValueError):
            continue
        if text:
            segments.append({"start": start, "text": text})
    segments.sort(key=lambda s: s["start"])
    return segments

def fetch_transcript_transcriptapi(video_id):
    """Fallback 1: TranscriptAPI (transcriptapi.com). Balik None kalau key belum diset."""
    if not TRANSCRIPT_API_KEY:
        return None
    last_error = "tidak ada respons"
    for lang in ("id,en", "asr-id,asr-en"):
        try:
            r = requests.get(
                "https://transcriptapi.com/api/v2/youtube/transcript",
                params={"video_url": video_id, "language": lang},
                headers={"Authorization": f"Bearer {TRANSCRIPT_API_KEY}"},
                timeout=30,
            )
            if r.status_code == 200:
                segments = _clean_segments(r.json().get("transcript"))
                if segments:
                    return segments
            last_error = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last_error = str(e)[:100]
    raise Exception(f"TranscriptAPI gagal ({last_error})")

def fetch_transcript_supadata(video_id):
    """Fallback 2: Supadata (supadata.ai). offset/duration mereka milidetik.
    Balik None kalau key belum diset."""
    if not SUPADATA_API_KEY:
        return None
    last_error = "tidak ada respons"
    for lang in ("id", "en"):
        try:
            r = requests.get(
                "https://api.supadata.ai/v1/transcript",
                params={
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "mode": "native",
                    "lang": lang,
                    "text": "false",
                },
                headers={"x-api-key": SUPADATA_API_KEY},
                timeout=30,
            )
            if r.status_code == 200:
                content = r.json().get("content")
                if isinstance(content, list):
                    segments = _clean_segments(content, start_key="offset", scale=1000.0)
                    if segments:
                        return segments
            last_error = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last_error = str(e)[:100]
    raise Exception(f"Supadata gagal ({last_error})")

_TS_PATTERN = r'(?:(\d{1,2}):)?([0-5]?\d):([0-5]\d)'

def _ts_to_seconds(h, m, s):
    return int(h or 0) * 3600 + int(m) * 60 + int(s)

def parse_ts(ts):
    """Parse 'M:SS' / 'H:MM:SS' / detik polos jadi float detik. None kalau invalid."""
    if isinstance(ts, (int, float)):
        return float(ts)
    m = re.match(r'^\s*(?:(\d{1,2}):)?([0-5]?\d):([0-5]\d)\s*$', str(ts or ""))
    if m:
        return float(_ts_to_seconds(m.group(1), m.group(2), m.group(3)))
    try:
        return float(str(ts).strip())
    except (TypeError, ValueError):
        return None

def parse_manual_transcript(text):
    """Parse transcript yang ditempel user. Mendukung:
    - WebVTT / SRT (ada panah -->)
    - Format salinan panel transkrip YouTube: baris "0:00 teks" atau
      timestamp dan teks bergantian baris."""
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []

    segments = []
    if "-->" in text:
        cue_re = re.compile(_TS_PATTERN + r'[.,]\d{1,3}\s*-->')
        matches = list(cue_re.finditer(text))
        for i, mt in enumerate(matches):
            start = _ts_to_seconds(mt.group(1), mt.group(2), mt.group(3))
            end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            block = text[mt.end():end_pos]
            # Sisa baris pertama setelah "-->" adalah timestamp selesai; buang.
            if "\n" in block:
                block = block.split("\n", 1)[1]
            lines = []
            for ln in block.splitlines():
                ln = re.sub(r'<[^>]+>', '', ln).strip()
                if ln and not ln.isdigit():
                    lines.append(ln)
            if lines:
                segments.append({"start": start, "text": " ".join(lines)})
        segments.sort(key=lambda s: s["start"])
        return segments

    line_re = re.compile(r'^[\[]?' + _TS_PATTERN + r'[\]]?\s*(.*)$')
    pending = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = line_re.match(line)
        if m:
            start = _ts_to_seconds(m.group(1), m.group(2), m.group(3))
            rest = m.group(4).strip()
            if rest:
                segments.append({"start": start, "text": rest})
                pending = None
            else:
                pending = start
        elif pending is not None:
            segments.append({"start": pending, "text": line})
            pending = None
        elif segments:
            segments[-1]["text"] += " " + line
    segments.sort(key=lambda s: s["start"])
    return segments

def get_transcript_segments(video_id, manual_text=""):
    """Sumber transcript berurutan: tempelan manual user -> library langsung
    (gratis, jalan dari IP rumahan) -> TranscriptAPI -> Supadata.
    Balik (segments, sumber)."""
    if manual_text:
        segments = parse_manual_transcript(manual_text)
        if len(segments) >= 5:
            return segments, "manual"

    try:
        return fetch_transcript_with_timestamps(video_id), "youtube"
    except Exception as e:
        direct_error = str(e)

    def _brief(msg):
        return " ".join(str(msg).split())[:120]

    errors = [_brief(direct_error)]
    for fn in (fetch_transcript_transcriptapi, fetch_transcript_supadata):
        try:
            segments = fn(video_id)
            if segments:
                return segments, fn.__name__.replace("fetch_transcript_", "")
        except Exception as e:
            errors.append(_brief(e))

    detail = "; ".join(errors)[:300]
    raise Exception(
        "Gagal ambil transcript otomatis. YouTube memblokir IP server (Vercel), "
        "dan API fallback belum aktif/gagal. Solusi: tempel transcript manual "
        "(kolom di bawah link video), atau isi TRANSCRIPT_API_KEY / SUPADATA_API_KEY "
        f"di Environment Variables. Detail: {detail}"
    )

def format_time(seconds):
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"

def segments_to_windows(segments, window_sec=60, step_sec=30):
    if not segments:
        return []
    max_time = segments[-1]['start'] + 10
    windows = []
    t = 0
    while t < max_time:
        texts = [s['text'] for s in segments if t <= s['start'] < t + window_sec]
        if texts:
            windows.append({"start": t, "end": min(t + window_sec, max_time), "text": ' '.join(texts)})
        t += step_sec
    return windows

def parse_clips(raw_text):
    """Parse output model jadi list clip yang bersih. Model diminta membalas
    JSON object {"clips": [...]}, tapi tetap disiapkan fallback ekstraksi array."""
    data = None
    try:
        data = json.loads(raw_text)
    except (ValueError, TypeError):
        match = re.search(r'\[.*\]', raw_text or '', re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except ValueError:
                data = None
    if isinstance(data, dict):
        data = data.get("clips")
    if not isinstance(data, list):
        return None

    clips = []
    for c in data:
        if not isinstance(c, dict):
            continue
        start = str(c.get("start", "")).strip()
        end = str(c.get("end", "")).strip()
        hook = str(c.get("hook", "")).strip()
        if not start or not end or not hook:
            continue
        try:
            score = int(round(float(c.get("score", 5))))
        except (ValueError, TypeError):
            score = 5
        clips.append({
            "start": start,
            "end": end,
            "hook": hook,
            "hashtags": str(c.get("hashtags", "")).strip(),
            "reason": str(c.get("reason", "")).strip(),
            "score": max(1, min(10, score)),
        })
    return clips or None

HOOK_STYLE_GUIDE = {
    "VTuber / Vstreamer": """Gaya hook VTuber/streamer: casual, ekspresif, boleh pake bahasa gaul berat. Fokus ke reaksi dan ekspresi streamer.
Contoh: "dia nangis beneran pas momen ini... gue ga nyangka 😭", "chat langsung rame parah pas ini kejadian HAHAHA" """,

    "Gaming": """Gaya hook gaming: hype, dramatis, fokus ke momen gameplay atau reaksi. Boleh pake istilah gaming.
Contoh: "ini clutch paling gila yang pernah gue liat seumur hidup", "dia solo semua musuh sendirian... caranya gila banget" """,

    "IRL / Just Chatting": """Gaya hook IRL: relatable, conversational, kayak lagi cerita ke temen.
Contoh: "tolong dia ngomong apa barusan 💀", "moment paling awkward di stream dan dia malah ketawa" """,

    "Olahraga / Highlight": """Gaya hook olahraga: dramatis tapi natural, fokus ke momen krusial, bisa pake nama pemain.
Contoh: "gue rewind ini 10 kali dan masih ga percaya itu bisa masuk", "detik-detik yang bikin semua orang di stadion diem seketika" """,

    "Podcast": """Gaya hook podcast: bikin penasaran, fokus ke statement mengejutkan atau insight yang jarang dibahas.
Contoh: "dia baru aja ngungkapin sesuatu yang belum pernah diomongin ke siapapun", "pendapat dia soal ini bikin gue mikir ulang segalanya" """,

    "Edukasi": """Gaya hook edukasi: bikin penasaran, frame kayak 'fakta yang bikin kaget' atau 'hal yang kamu ga tau'.
Contoh: "ternyata yang selama ini kita percaya itu salah total", "cara kerja ini bikin gue sadar betapa bodohnya gue selama ini 😭" """,

    "Musik / Concert": """Gaya hook musik: emosional, vivid, fokus ke momen yang bikin bulu kuduk berdiri.
Contoh: "bagian ini setiap kali gue denger selalu bikin merinding", "dia improvisasi di sini dan hasilnya luar biasa banget" """,

    "Vlog": """Gaya hook vlog: personal, storytelling, bikin penonton ngerasa ikut ngalamin.
Contoh: "gue ga nyangka hari itu bakal berakhir kayak gini", "ini momen yang bikin gue sadar harus berubah" """,

    "Berita / Current Events": """Gaya hook berita: to the point tapi tetap bikin penasaran, hindari judul koran.
Contoh: "tunggu sampai lo denger apa yang dia bilang di bagian ini", "reaksi dia setelah denger kabar ini bikin semua orang kaget" """,

    "Review / Tech": """Gaya hook review/tech: fokus ke temuan mengejutkan atau perbandingan yang bikin penasaran.
Contoh: "gue ga nyangka hasilnya bakal separah ini pas ditest", "ini alasan kenapa gue ga bakal rekomendasiin produk ini ke siapapun" """,

    "Variety / Campuran": """Gaya hook variety: fleksibel, fokus ke momen paling memorable atau mengejutkan.
Contoh: "ini momen yang bikin semua orang langsung diem", "ga ada yang nyangka ini bakal kejadian hari ini" """
}


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/capabilities")
def capabilities():
    return jsonify({
        "transcript_api": bool(TRANSCRIPT_API_KEY or SUPADATA_API_KEY),
        "whisper": bool(os.getenv("GROQ_API_KEY")),
        "serverless": bool(os.getenv("VERCEL")),
        "local_cut": bool(LOCAL_CUT),
    })

WHISPER_MODEL = os.getenv("GROQ_WHISPER_MODEL", "whisper-large-v3-turbo")

@app.route("/api/transcribe", methods=["POST"])
def transcribe():
    """Speech-to-text satu potongan audio via Groq Whisper.

    Browser mengekstrak audio dari file video (ffmpeg.wasm) dan mengirimnya
    per potongan (audio mono 32kbps +/- 1,9MB per 8 menit, aman dari batas
    body Vercel 4,5MB dan batas file Groq 25MB). Server menambahkan offset
    supaya timestamp segmen kembali ke posisi aslinya di video.
    """
    key = os.getenv("GROQ_API_KEY")
    if not key:
        return jsonify({"error": "GROQ_API_KEY belum di-set di server."}), 500
    f = request.files.get("file")
    if f is None:
        return jsonify({"error": "File audio tidak diterima."}), 400
    data_bytes = f.read()
    if not data_bytes:
        return jsonify({"error": "File audio kosong."}), 400
    if len(data_bytes) > 25 * 1024 * 1024:
        return jsonify({"error": "Potongan audio terlalu besar (maks 25MB)."}), 400
    try:
        offset = float(request.form.get("offset", 0))
    except (ValueError, TypeError):
        offset = 0.0
    lang = (request.form.get("lang") or "").strip()

    payload = {
        "model": WHISPER_MODEL,
        "response_format": "verbose_json",
        "timestamp_granularities[]": "segment",
    }
    if lang:
        payload["language"] = lang
    try:
        resp = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": (f.filename or "chunk.m4a", data_bytes, f.mimetype or "audio/mp4")},
            data=payload,
            timeout=120,
        )
    except requests.Timeout:
        return jsonify({"error": "Whisper timeout, coba lagi."}), 504
    except Exception as e:
        return jsonify({"error": f"Gagal menghubungi Whisper: {e}"}), 502

    try:
        result = resp.json()
    except ValueError:
        result = {}
    if resp.status_code != 200:
        msg = ""
        if isinstance(result, dict):
            msg = (result.get("error") or {}).get("message", "")
        if resp.status_code == 401:
            msg = "API key Groq tidak valid / sudah mati. Cek GROQ_API_KEY di server."
        return jsonify({"error": msg or f"Whisper error (HTTP {resp.status_code})."}), 502

    segments = []
    for s in (result.get("segments") or []):
        if not isinstance(s, dict):
            continue
        try:
            start = float(s.get("start", 0)) + offset
            end = float(s.get("end", 0)) + offset
        except (ValueError, TypeError):
            continue
        text = str(s.get("text", "")).strip()
        if text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": text})
    return jsonify({"segments": segments, "duration": result.get("duration")})

@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    platform = data.get("platform", "YouTube Shorts")
    content_type = data.get("contentType", "Variety / Campuran")
    try:
        max_clips = int(data.get("maxClips", 5))
    except (ValueError, TypeError):
        max_clips = 5
    max_clips = max(1, min(10, max_clips))
    try:
        target_duration = int(data.get("targetDuration", 60))
    except (ValueError, TypeError):
        target_duration = 60
    target_duration = max(15, min(180, target_duration))

    segments = None
    transcript_source = None
    video_id = None
    direct = data.get("segments")
    if isinstance(direct, list) and direct:
        cleaned = []
        for s in direct:
            if not isinstance(s, dict):
                continue
            try:
                st = float(s.get("start", 0))
            except (ValueError, TypeError):
                continue
            tx = str(s.get("text", "")).strip()
            if tx:
                cleaned.append({"start": st, "text": tx})
        if len(cleaned) >= 5:
            segments = cleaned
            transcript_source = "whisper"
            video_id = str(data.get("videoId") or "file")

    if segments is None:
        if not url:
            return jsonify({"error": "URL tidak boleh kosong!"}), 400
        video_id = extract_video_id(url)
        if not video_id:
            return jsonify({"error": "URL bukan video YouTube yang valid!"}), 400
        manual_text = str(data.get("transcript", ""))[:200_000]
        try:
            segments, transcript_source = get_transcript_segments(video_id, manual_text)
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    if len(segments) < 5:
        return jsonify({"error": "Transcript terlalu pendek untuk dianalisis!"}), 400

    windows = segments_to_windows(segments, window_sec=60, step_sec=30)
    if len(windows) > 40:
        step = len(windows) // 40
        windows = windows[::step][:40]

    windows_text = ""
    for w in windows:
        windows_text += f"[{format_time(w['start'])} - {format_time(w['end'])}] {w['text'][:300]}\n\n"

    hook_guide = HOOK_STYLE_GUIDE.get(content_type, HOOK_STYLE_GUIDE["Variety / Campuran"])

    prompt = f"""Lo adalah clipper berpengalaman yang spesialis konten {content_type} dan udah tau persis cara bikin hook yang bikin orang stop scrolling di {platform}.

{hook_guide}

Platform target: {platform}
Jenis konten: {content_type}
Target durasi tiap clip: sekitar {target_duration} detik (end = start + kurang lebih {target_duration} detik; boleh meleset dikit demi momen yang utuh).

Transcript video dengan timestamp:
---
{windows_text}
---

Tugas lo: pilih {max_clips} momen terbaik yang paling layak dijadiin clip.

Untuk tiap rekomendasi berikan:
- "hook": judul/caption NATURAL sesuai gaya {content_type} di atas. JANGAN kaku kayak judul berita. Panjang 60-120 karakter.
- "hashtags": 8-12 hashtag relevan untuk {platform}, mix antara hashtag besar dan spesifik. Format: "#tag1 #tag2 #tag3"
- "start": timestamp mulai (M:SS atau H:MM:SS)
- "end": timestamp selesai
- "reason": 1-2 kalimat kenapa momen ini bagus, tulis santai
- "score": potensi viral 1-10

Balas HANYA dengan JSON object persis seperti ini, tanpa teks lain:
{{"clips": [{{"start":"0:00","end":"0:45","hook":"...","hashtags":"#tag1 #tag2","reason":"...","score":8}}]}}"""

    try:
        raw = call_groq(prompt, max_tokens=2500)
        clips = parse_clips(raw)
        if not clips:
            return jsonify({"error": "Format response tidak valid, coba lagi!"}), 500
        clean_segments = [{"start": float(s.get("start", 0)), "text": str(s.get("text", ""))} for s in segments[:3000]]
        return jsonify({"clips": clips, "video_id": video_id, "total_segments": len(segments),
                        "transcript_source": transcript_source, "segments": clean_segments})
    except requests.Timeout:
        return jsonify({"error": "Timeout, coba lagi!"}), 504
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def parse_hooks(raw_text):
    """Parse output generator hook jadi list bersih [{"text": ..., "angle": ...}]."""
    data = None
    try:
        data = json.loads(raw_text)
    except (ValueError, TypeError):
        match = re.search(r'\[.*\]', raw_text or '', re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except ValueError:
                data = None
    if isinstance(data, dict):
        data = data.get("hooks")
    if not isinstance(data, list):
        return None
    hooks = []
    for item in data:
        if isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            angle = str(item.get("angle", "")).strip()
        else:
            text, angle = str(item).strip(), ""
        if text:
            hooks.append({"text": text, "angle": angle})
    return hooks or None

@app.route("/api/hooks", methods=["POST"])
def gen_hooks():
    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()[:4000]
    platform = data.get("platform", "TikTok")
    content_type = data.get("contentType", "Variety / Campuran")
    try:
        count = int(data.get("count", 10))
    except (ValueError, TypeError):
        count = 10
    count = max(3, min(15, count))

    if len(text) < 10:
        return jsonify({"error": "Isi dulu topik / cuplikan isi videonya (minimal 10 karakter)."}), 400

    hook_guide = HOOK_STYLE_GUIDE.get(content_type, HOOK_STYLE_GUIDE["Variety / Campuran"])

    prompt = f"""Lo adalah copywriter hook buat konten {content_type} di {platform} — spesialis bikin orang berhenti scroll dalam 2 detik pertama.

{hook_guide}

Bahan (topik / cuplikan isi video):
---
{text}
---

Bikin {count} variasi hook yang beda-beda sudut pandangnya. Variasikan sudut dari daftar ini: rasa penasaran, fakta mengejutkan, pertanyaan retoris, relatable keseharian, angka/spesifik, statement berani, perbandingan, kesalahan umum yang orang lakuin.

Aturan:
- Tiap hook 60-120 karakter, bahasa santai natural, JANGAN kaku kayak judul berita.
- Jangan pakai hashtag di dalam hook.
- Tiap hook harus beda rasa, bukan ganti kata doang.

Balas HANYA dengan JSON object persis seperti ini, tanpa teks lain:
{{"hooks": [{{"text": "...", "angle": "rasa penasaran"}}]}}"""

    # Model reasoning bisa kehabisan token sebelum JSON selesai (Groq balas
    # "Failed to validate JSON") — token dibuat lega + retry sekali.
    hooks = None
    last_err = None
    for attempt in range(2):
        try:
            raw = call_groq(prompt, max_tokens=3000)
            hooks = parse_hooks(raw)
            if hooks:
                break
            last_err = Exception("Format response tidak valid, coba lagi!")
        except requests.Timeout:
            return jsonify({"error": "Timeout, coba lagi!"}), 504
        except Exception as e:
            last_err = e
            if "validate JSON" not in str(e):
                break
    if not hooks:
        return jsonify({"error": str(last_err) if last_err else "Gagal generate hook, coba lagi!"}), 500
    return jsonify({"hooks": hooks})

# =====================================================================
# MODE LOKAL (Termux / PC sendiri) — potong langsung dari link.
#
# Di serverless (Vercel) fitur ini MATI by design: tidak ada ffmpeg/yt-dlp
# dan IP cloud diblokir YouTube. Di Termux, server jalan di HP user dengan
# IP rumahan: yt-dlp download video sumber, ffmpeg beneran yang memotong
# dan menggambar caption (drawtext) — bukan mesin browser.
# =====================================================================
import shutil, subprocess, tempfile, threading, time, uuid

_LOCAL_FFMPEG = shutil.which("ffmpeg")
_LOCAL_YTDLP = shutil.which("yt-dlp")


def _ffmpeg_has_drawtext():
    if not _LOCAL_FFMPEG:
        return False
    try:
        out = subprocess.run([_LOCAL_FFMPEG, "-hide_banner", "-filters"],
                             capture_output=True, text=True, timeout=20).stdout
        return " drawtext " in out
    except Exception:
        return False


LOCAL_CUT = bool(_LOCAL_FFMPEG and _LOCAL_YTDLP and _ffmpeg_has_drawtext()
                 and not os.getenv("VERCEL"))

_FONT_DIRS = [
    "/data/data/com.termux/files/usr/share/fonts/TTF",
    "/data/data/com.termux/files/usr/share/fonts",
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/TTF",
]


def _find_font(bold=True, mono=False):
    names = (["DejaVuSansMono-Bold.ttf", "DejaVuSansMono.ttf"] if mono else
             ["DejaVuSans-Bold.ttf", "DejaVuSans.ttf"] if bold else
             ["DejaVuSans.ttf"])
    for d in _FONT_DIRS:
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return p
    return None


# Pemetaan gaya caption (kunci sama dengan CAP_STYLE_CFG di frontend).
# fs/border dihitung relatif terhadap tinggi output.
SERVER_STYLE = {
    "klasik":  {"fs": 0.040, "color": "0xffffff", "border_ratio": 0.16, "border_color": "0x000000", "box": 0, "upper": False, "center": False, "bold": True, "mono": False},
    "kotak":   {"fs": 0.040, "color": "0xffffff", "border_ratio": 0, "border_color": "0x000000", "box": 1, "box_color": "black@0.72", "upper": False, "center": False, "bold": True, "mono": False},
    "pop":     {"fs": 0.062, "color": "0xffe033", "border_ratio": 0.20, "border_color": "0x000000", "box": 0, "upper": True, "center": False, "bold": True, "mono": False},
    "tengah":  {"fs": 0.068, "color": "0xffffff", "border_ratio": 0.15, "border_color": "0x000000", "box": 0, "upper": False, "center": True, "bold": True, "mono": False},
    "minimal": {"fs": 0.030, "color": "0xffffff", "border_ratio": 0, "border_color": "0x000000", "box": 0, "upper": False, "center": False, "bold": False, "mono": False},
    "neon":    {"fs": 0.058, "color": "0x4df3ff", "border_ratio": 0.10, "border_color": "0x032630", "box": 0, "upper": True, "center": False, "bold": True, "mono": False},
    "api":     {"fs": 0.055, "color": "0xffa62b", "border_ratio": 0.18, "border_color": "0x000000", "box": 0, "upper": False, "center": False, "bold": True, "mono": False},
    "impact":  {"fs": 0.066, "color": "0xff4040", "border_ratio": 0.24, "border_color": "0x000000", "box": 0, "upper": True, "center": False, "bold": True, "mono": False},
    "invers":  {"fs": 0.040, "color": "0x0c0d10", "border_ratio": 0, "border_color": "0x000000", "box": 1, "box_color": "white@0.94", "upper": False, "center": False, "bold": True, "mono": False},
    "mesin":   {"fs": 0.034, "color": "0xc8ffd9", "border_ratio": 0.10, "border_color": "0x04150a", "box": 0, "upper": True, "center": False, "bold": False, "mono": True},
}

_JOBS = {}
_JOBS_LOCK = threading.Lock()
_JOBS_DIR = os.path.join(tempfile.gettempdir(), "klipin_local_jobs")
os.makedirs(_JOBS_DIR, exist_ok=True)


def _build_cues_py(segments, start, end, upper):
    """Port buildCues dari frontend: potong segmen jadi cue maks 6 kata."""
    cues = []
    in_range = [s for s in (segments or []) if isinstance(s, dict)
                and start - 1 <= float(s.get("start", 0)) < end]
    for i, s in enumerate(in_range):
        s_start = float(s.get("start", 0))
        nxt = float(in_range[i + 1].get("start", 0)) if i + 1 < len(in_range) else s_start + 4
        cs = max(s_start, start) - start
        ce = min(nxt, end) - start
        text = re.sub(r"\s+", " ", str(s.get("text", ""))).strip()
        if not text or ce <= cs:
            continue
        if upper:
            text = text.upper()
        words = text.split(" ")
        chunks = [" ".join(words[w:w + 6]) for w in range(0, len(words), 6)]
        acc = 0.0
        for ch in chunks:
            frac = len(ch.split(" ")) / len(words)
            t0 = cs + (ce - cs) * acc
            t1 = cs + (ce - cs) * (acc + frac)
            acc += frac
            if t1 > t0:
                cues.append((round(t0, 2), round(t1, 2), ch))
    return cues[:120]


def _source_path(video_id):
    return os.path.join(_JOBS_DIR, "_src_" + video_id + ".mp4")


def _ensure_source(video_id, job):
    path = _source_path(video_id)
    if os.path.exists(path) and os.path.getsize(path) > 100000:
        return path
    job["phase"] = "Mengunduh video sumber dari YouTube…"
    url = "https://www.youtube.com/watch?v=" + video_id
    cmd = [_LOCAL_YTDLP, "-f", "bv*[height<=720]+ba/b[height<=720]/b",
           "--merge-output-format", "mp4", "--no-playlist", "--no-warnings",
           "-o", path, url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not os.path.exists(path):
        tail = (r.stderr or r.stdout or "")[-300:]
        raise RuntimeError("Download video sumber gagal. " + tail)
    return path


def _wrap_cue_text(text, cpl):
    words = text.split(" ")
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if len(t) > cpl and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return "\n".join(lines[:4])


def _cut_one(src, start, end, out_path, style_key, vertical, segments, workdir):
    st = SERVER_STYLE.get(style_key) or SERVER_STYLE["klasik"]
    out_h = 1920 if vertical else 720
    out_w = 1080 if vertical else 1280
    font = _find_font(bold=st["bold"], mono=st["mono"])
    cues = _build_cues_py(segments, start, end, st["upper"]) if (segments and font) else []
    if vertical:
        graph = ("[0:v]split=2[a][b];"
                 "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,gblur=sigma=40[bg];"
                 "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2[base]")
    else:
        graph = "[0:v]scale=-2:'min(720,ih)'[base]"
    prev = "base"
    fs = max(18, int(out_h * st["fs"]))
    bw = max(0, int(fs * st["border_ratio"]))
    # drawtext tidak membungkus teks: perkirakan lebar per karakter lalu
    # bungkus cue jadi maks 4 baris supaya tidak luber keluar frame.
    # (DejaVu itu font lebar: faktornya lebih besar dari font sans biasa.)
    if st["mono"]:
        char_w = 0.62
    elif st["upper"]:
        char_w = 0.80
    elif st["bold"]:
        char_w = 0.72
    else:
        char_w = 0.66
    cpl = max(6, int((out_w * 0.90) / (fs * char_w)))
    for n, (t0, t1, text) in enumerate(cues):
        tf = os.path.join(workdir, "cue_%d.txt" % n)
        with open(tf, "w", encoding="utf-8") as f:
            f.write(_wrap_cue_text(text, cpl))
        y = "h*0.56-text_h/2" if st["center"] else "h-text_h-h*0.07"
        dt = ("drawtext=fontfile='%s':textfile='cue_%d.txt':fontsize=%d:fontcolor=%s"
              ":x=(w-text_w)/2:y=%s:enable='between(t,%.2f,%.2f)'"
              % (font, n, fs, st["color"], y, t0, t1))
        if bw:
            dt += ":borderw=%d:bordercolor=%s" % (bw, st["border_color"])
        if st.get("box"):
            dt += ":box=1:boxcolor=%s:boxborderw=%d" % (st.get("box_color", "black@0.72"), max(8, fs // 2))
        graph += ";[%s]%s[d%d]" % (prev, dt, n)
        prev = "d%d" % n
    graph += ";[%s]format=yuv420p[vout]" % prev
    cmd = [_LOCAL_FFMPEG, "-y", "-ss", str(start), "-to", str(end), "-i", src,
           "-filter_complex", graph, "-map", "[vout]", "-map", "0:a?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", out_path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, cwd=workdir)
    if r.returncode != 0 or not os.path.exists(out_path):
        tail = (r.stderr or "")[-300:]
        raise RuntimeError("ffmpeg gagal memotong. " + tail)
    return out_path


def _run_local_job(job_id, payload):
    job = _JOBS[job_id]
    try:
        src = _ensure_source(payload["video_id"], job)
        workdir = os.path.join(_JOBS_DIR, job_id)
        os.makedirs(workdir, exist_ok=True)
        files = []
        for i, c in enumerate(payload["clips"]):
            job["phase"] = "Memotong clip %d dari %d…" % (i + 1, len(payload["clips"]))
            name = "klipin-%s-%02d.mp4" % (payload["video_id"], i + 1)
            out = os.path.join(workdir, name)
            _cut_one(src, c["start"], c["end"], out, payload["style"],
                     payload["vertical"],
                     payload["segments"] if payload["captions"] else [], workdir)
            files.append({"name": name, "path": out})
        job["files"] = files
        job["phase"] = "Selesai"
        job["status"] = "done"
    except Exception as e:
        job["status"] = "error"
        job["error"] = str(e)[:400]
        job["phase"] = "Gagal"


@app.route("/api/local/jobs", methods=["POST"])
def local_job_create():
    if not LOCAL_CUT:
        return jsonify({"error": "Mode potong langsung hanya aktif saat Klipin jalan lokal (Termux/PC) dengan ffmpeg + yt-dlp. Di Vercel fitur ini mati by design."}), 400
    data = request.get_json(force=True, silent=True) or {}
    video_id = str(data.get("video_id") or "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
        return jsonify({"error": "video_id tidak valid."}), 400
    clips = []
    for c in (data.get("clips") or [])[:6]:
        try:
            s = parse_ts(str(c.get("start", "")))
            e = parse_ts(str(c.get("end", "")))
        except Exception:
            continue
        if e > s >= 0 and e - s <= 3600:
            clips.append({"start": s, "end": e})
    if not clips:
        return jsonify({"error": "Tidak ada clip yang valid."}), 400
    segs = []
    for s in (data.get("segments") or [])[:4000]:
        if isinstance(s, dict) and "start" in s:
            try:
                segs.append({"start": float(s["start"]), "text": str(s.get("text", ""))})
            except Exception:
                pass
    job_id = uuid.uuid4().hex[:12]
    payload = {"video_id": video_id, "clips": clips,
               "style": str(data.get("style") or "klasik"),
               "vertical": bool(data.get("vertical", True)),
               "captions": bool(data.get("captions", True)),
               "segments": segs}
    with _JOBS_LOCK:
        _JOBS[job_id] = {"status": "working", "phase": "Menyiapkan…", "error": "", "files": []}
    threading.Thread(target=_run_local_job, args=(job_id, payload), daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/local/jobs/<job_id>")
def local_job_status(job_id):
    job = _JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job tidak ditemukan (server mungkin restart)."}), 404
    return jsonify({"status": job["status"], "phase": job["phase"],
                    "error": job["error"],
                    "files": [f["name"] for f in job["files"]]})


@app.route("/api/local/jobs/<job_id>/file/<int:idx>")
def local_job_file(job_id, idx):
    job = _JOBS.get(job_id)
    if not job or idx >= len(job["files"]):
        return jsonify({"error": "File tidak ditemukan."}), 404
    f = job["files"][idx]
    return send_file(f["path"], as_attachment=True, download_name=f["name"])


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5003)), debug=os.environ.get("DEBUG") == "1")
