import datetime
import io
import json
import math
import os
import random
import urllib.error
import urllib.request

import pandas as pd
from flask import Flask, jsonify, request, send_file

try:
    from flask_cors import CORS
    HAS_CORS = True
except ImportError:
    HAS_CORS = False

try:
    from sklearn.neighbors import KNeighborsClassifier
    from sklearn.preprocessing import StandardScaler
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

app = Flask(__name__)
if HAS_CORS:
    CORS(app)
else:
    @app.after_request
    def add_cors(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type,x-api-key"
        response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        return response

BASE = os.path.dirname(os.path.abspath(__file__))
SONGS_PATH = os.path.join(BASE, "songs.csv")
MODEL_PATH = os.path.join(BASE, "mood_model.pkl")
EXTERNAL_AI_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
EXTERNAL_MODEL = "cl" + "aude-sonnet-4-6"

MOOD_META = {
    "overwhelmed": {"color": "#3B6EFF", "emoji": "", "description": "Too much at once. Let the music carry you.", "journal_prompt": "What is taking up the most mental space right now? Write it down and let it go.", "activity": "Try box breathing: inhale 4 counts, hold 4, exhale 4, hold 4.", "vibe": "calm, grounding", "target": {"valence": 0.30, "energy": 0.30, "tempo": 70}},
    "lonely": {"color": "#A29BFE", "emoji": "", "description": "You're not alone in feeling alone.", "journal_prompt": "Who is one person you could reach out to today? What would you want to say?", "activity": "Write a letter to your past self — no need to send it.", "vibe": "warm, intimate", "target": {"valence": 0.22, "energy": 0.28, "tempo": 72}},
    "anxious": {"color": "#5EE5FF", "emoji": "", "description": "Your nervous system needs a signal. Music is it.", "journal_prompt": "What is the worst that could actually happen? Write it out, then write the most likely outcome.", "activity": "Name 5 things you can see, 4 you can touch, 3 you can hear.", "vibe": "steady, focused", "target": {"valence": 0.32, "energy": 0.45, "tempo": 90}},
    "tired": {"color": "#8B93B8", "emoji": "", "description": "Rest is productive. Put this on and close your eyes.", "journal_prompt": "What drained you most today? What would 'enough' look like right now?", "activity": "Lie down for 10 minutes. No phone. Just breath and sound.", "vibe": "slow, restful", "target": {"valence": 0.35, "energy": 0.17, "tempo": 65}},
    "happy": {"color": "#FFD93D", "emoji": "", "description": "Ride this wave. You've earned it.", "journal_prompt": "What made you smile today? How can you bring more of that into tomorrow?", "activity": "Text someone a genuine compliment right now.", "vibe": "energetic, joyful", "target": {"valence": 0.84, "energy": 0.77, "tempo": 120}},
    "heartbroken": {"color": "#74B9FF", "emoji": "", "description": "Heartbreak is love with nowhere to go. We've got you.", "journal_prompt": "What do you miss? What are you still holding onto that you could gently release?", "activity": "Hold something warm — a mug of tea, a blanket. Just be with yourself.", "vibe": "tender, honest", "target": {"valence": 0.17, "energy": 0.25, "tempo": 72}},
    "focus": {"color": "#00CEC9", "emoji": "", "description": "Block the noise. Enter the zone.", "journal_prompt": "What is the one thing that would make today feel complete if done?", "activity": "Set a 25-minute Pomodoro timer. One task. Full attention.", "vibe": "clear, instrumental", "target": {"valence": 0.45, "energy": 0.42, "tempo": 100}},
    "healing": {"color": "#A29BFE", "emoji": "", "description": "You're getting there. One song at a time.", "journal_prompt": "Write three things you're proud of yourself for this week, however small.", "activity": "Step outside for 5 minutes. Notice what's growing around you.", "vibe": "soft, hopeful", "target": {"valence": 0.55, "energy": 0.27, "tempo": 77}},
}


def load_songs():
    df = pd.read_csv(SONGS_PATH)
    df.columns = [c.strip() for c in df.columns]
    return df


def duration_label(seconds):
    s = int(float(seconds))
    return f"{s // 60}:{s % 60:02d}"


def score_song(row, mood):
    target = MOOD_META[mood]["target"]
    dv = (row["valence"] - target["valence"]) * 2.5
    de = (row["energy"] - target["energy"]) * 2.0
    dt = (row["tempo"] - target["tempo"]) / 120.0
    dist = math.sqrt(dv ** 2 + de ** 2 + dt ** 2)
    dance_bonus = -row.get("danceability", 0.5) * 0.3 if mood in ("happy", "focus") else 0.0
    acoustic_bonus = -row.get("acousticness", 0.5) * 0.2 if mood in ("overwhelmed", "tired", "healing") else 0.0
    return dist + dance_bonus + acoustic_bonus


def row_to_song(row):
    return {
        "id": int(row["song_id"]),
        "title": row["title"],
        "artist": row["artist"],
        "genre": row["genre"],
        "duration": duration_label(row["duration_sec"]),
        "valence": round(float(row["valence"]), 2),
        "energy": round(float(row["energy"]), 2),
        "tempo": round(float(row["tempo"]), 1),
        "danceability": round(float(row.get("danceability", 0.5)), 2),
        "acousticness": round(float(row.get("acousticness", 0.5)), 2),
        "loudness": round(float(row.get("loudness", -12)), 2),
        "instrumentalness": round(float(row.get("instrumentalness", 0.05)), 2),
        "speechiness": round(float(row.get("speechiness", 0.05)), 2),
        "cover": str(row.get("cover_url", "")),
        "spotify_url": str(row.get("spotify_url", "")),
    }


def get_recommendations(mood, n=8, randomize=False):
    df = load_songs()
    pool = df[df["primary_mood"].str.lower() == mood].copy()
    if pool.empty:
        return []
    pool["_score"] = pool.apply(lambda r: score_song(r, mood), axis=1)
    if randomize:
        pool["_weight"] = 1.0 / (pool["_score"] + 0.1)
        selected = pool.sample(n=min(n, len(pool)), weights="_weight", replace=False)
    else:
        selected = pool.nsmallest(n, "_score").sample(frac=1).reset_index(drop=True)
    return [row_to_song(row) for _, row in selected.iterrows()]


_knn_model = None
_knn_scaler = None
_knn_features = ["valence", "energy", "tempo", "danceability", "acousticness", "loudness", "instrumentalness", "speechiness"]


def build_knn():
    global _knn_model, _knn_scaler, _knn_features
    if not HAS_SKLEARN:
        return
    if os.path.exists(MODEL_PATH):
        try:
            import pickle
            with open(MODEL_PATH, "rb") as f:
                bundle = pickle.load(f)
            _knn_model = bundle["model"]
            _knn_scaler = bundle["scaler"]
            _knn_features = bundle.get("features", _knn_features)
            return
        except Exception:
            pass
    df = load_songs()
    x = df[_knn_features].fillna(0.5).values
    y = df["primary_mood"].str.lower().values
    _knn_scaler = StandardScaler()
    xs = _knn_scaler.fit_transform(x)
    _knn_model = KNeighborsClassifier(n_neighbors=3, weights="distance", metric="euclidean")
    _knn_model.fit(xs, y)


build_knn()


def call_external_ai(system, user_msg, max_tokens=200):
    if not EXTERNAL_AI_KEY:
        return ""
    payload = json.dumps({"model": EXTERNAL_MODEL, "max_tokens": max_tokens, "system": system, "messages": [{"role": "user", "content": user_msg}]}).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={"Content-Type": "application/json", "x-api-key": EXTERNAL_AI_KEY, "anthropic-version": "2023-06-01"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            return data["content"][0]["text"]
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, IndexError, json.JSONDecodeError):
        return ""


def local_ai_response(mood, question, track=None):
    responses = {
        "overwhelmed": ["Perasaan kewalahan itu nyata. Pilih satu hal yang paling penting sekarang, lalu biarkan musik membantu napasmu melambat.", "Otakmu sedang membawa terlalu banyak beban. Tulis satu pikiran yang paling keras, lalu ambil satu langkah kecil."],
        "lonely": ["Merasa sendirian bukan berarti kamu sulit dicintai. Coba kirim satu pesan singkat ke seseorang yang terasa aman bagimu.", "Mood ini perlu kelembutan. Biarkan lagu ini menemanimu sambil kamu memutuskan siapa yang bisa kamu hubungi."],
        "anxious": ["Tubuhmu mungkin butuh bukti bahwa kamu aman. Tarik napas perlahan, lalu perhatikan tiga hal nyata di sekitarmu sekarang.", "Kecemasan sering melompat ke masa depan. Tarik balik ke satu tindakan konkret yang bisa kamu selesaikan hari ini."],
        "tired": ["Kamu tidak perlu memaksa diri untuk terlihat kuat. Turunkan ritme sebentar dan pilih versi paling ringan dari tugas berikutnya.", "Lelah adalah informasi, bukan kegagalan. Biarkan musik menjadi sinyal bahwa tubuhmu boleh melambat."],
        "happy": ["Pertahankan perasaan baik ini. Simpan lagu ini atau kirimkan ke seseorang yang bisa ikut merasakan energinya.", "Ini momen bagus untuk memperhatikan apa yang memberimu energi. Detail kecil itu bisa menjadi ritual yang bisa diulang."],
        "heartbroken": ["Patah hati datang dalam gelombang. Kamu tidak perlu menyelesaikan seluruh perasaan ini malam ini.", "Merindukan seseorang bisa terasa nyata bahkan ketika melanjutkan hidup adalah hal yang tepat. Jujurlah pada dirimu, lalu lembutlah."],
        "focus": ["Gerak terbaikmu adalah menghapus satu gangguan sebelum memulai. Satu tab, satu tugas, satu lagu, lalu mulai.", "Gunakan lagu ini seperti gerbang fokus. Mulai dari versi terkecil dari pekerjaan itu."],
        "healing": ["Penyembuhan sering terlihat pelan sebelum terasa besar. Perhatikan satu tanda kecil bahwa kamu sudah lebih kuat dari sebelumnya.", "Kamu boleh tumbuh dengan pelan. Pilih satu tindakan kecil yang membuat hari esok sedikit lebih ringan."],
    }
    pool = responses.get(mood, responses["healing"])
    idx = sum(ord(c) for c in question) % len(pool)
    music_line = f' Lagu "{track.get("title", "")}" bisa jadi ankermu sekarang.' if track else ""
    return pool[idx] + music_line


def rtf_escape(value):
    return str(value).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def build_rtf(mood, text, song_info, date_str, time_str):
    mood_emoji = MOOD_META.get(mood, MOOD_META["healing"])["emoji"]
    lines = rtf_escape(text).split("\n")
    paragraphs = "\n".join(f"{{\\f1\\fs24 {line}}}\\par" for line in lines)
    rtf = (
        r"{\rtf1\ansi\deff0" + "\n"
        r"{\fonttbl{\f0\froman\fcharset0 Times New Roman;}{\f1\fswiss\fcharset0 Arial;}}" + "\n"
        r"{\colortbl;\red59\green110\blue255;\red107\green117\blue168;\red0\green0\blue0;}" + "\n"
        r"\paperw12240\paperh15840\margl1800\margr1800\margt1440\margb1440" + "\n"
        r"\pard\qc{\f1\fs36\b\cf1 MoodBeat Journal " + mood_emoji + r"}\par" + "\n"
        r"\pard\qc{\f1\fs20\cf2 " + rtf_escape(date_str) + r" \bullet " + rtf_escape(time_str) + r"}\par" + "\n"
        r"\par" + "\n"
        r"\pard{\f1\fs24\b Mood: " + rtf_escape(mood.capitalize()) + " " + mood_emoji + r"}\par" + "\n"
        r"{\f1\fs20\cf2 Song companion: " + rtf_escape(song_info) + r"}\par" + "\n"
        r"\par" + "\n"
        r"\pard\brdrb\brdrs\brdrw10\brdrsp20{\f1\fs22\b\cf1 Journal Entry}\par" + "\n"
        r"\par" + "\n"
        + paragraphs + "\n"
        r"\par" + "\n"
        r"\pard\qc{\f1\fs16\i\cf2 Generated by MoodBeat \emdash  Understand your mood through music.}\par" + "\n"
        r"}"
    )
    return rtf.encode("latin-1", errors="replace")


@app.route("/api/moods", methods=["GET"])
def get_moods():
    safe_meta = {k: {kk: vv for kk, vv in v.items() if kk != "target"} for k, v in MOOD_META.items()}
    return jsonify({"moods": list(MOOD_META.keys()), "meta": safe_meta})


@app.route("/api/songs/<mood>", methods=["GET"])
def get_songs_for_mood(mood):
    mood = mood.lower().strip()
    if mood not in MOOD_META:
        return jsonify({"error": f"Unknown mood. Valid: {list(MOOD_META.keys())}"}), 400
    df = load_songs()
    songs = df[df["primary_mood"].str.lower() == mood]
    return jsonify({"mood": mood, "count": len(songs), "songs": [row_to_song(row) for _, row in songs.iterrows()]})


@app.route("/api/recommend", methods=["POST", "OPTIONS"])
def recommend():
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(silent=True) or {}
    mood = str(data.get("mood", "")).lower().strip()
    n = min(max(int(data.get("n", 8)), 1), 20)
    randomize = bool(data.get("randomize", False))
    if mood not in MOOD_META:
        return jsonify({"error": f"Unknown mood. Choose from: {list(MOOD_META.keys())}"}), 400
    songs = get_recommendations(mood, n, randomize=randomize)
    meta = {k: v for k, v in MOOD_META[mood].items() if k != "target"}
    return jsonify({"mood": mood, **meta, "songs": songs, "randomized": randomize})


@app.route("/api/classify", methods=["POST", "OPTIONS"])
def classify():
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(silent=True) or {}
    features = data.get("features", {})
    try:
        valence = float(features.get("valence", 0.5))
        energy = float(features.get("energy", 0.5))
        tempo = float(features.get("tempo", 90))
        danceability = float(features.get("danceability", 0.5))
        acousticness = float(features.get("acousticness", 0.5))
        speechiness = float(features.get("speechiness", 0.05))
    except (TypeError, ValueError) as exc:
        return jsonify({"error": str(exc)}), 400
    if HAS_SKLEARN and _knn_model is not None:
        feat_vals = {"valence": valence, "energy": energy, "tempo": tempo, "danceability": danceability, "acousticness": acousticness, "loudness": float(features.get("loudness", -12)), "instrumentalness": float(features.get("instrumentalness", 0.05)), "speechiness": speechiness}
        x = [[feat_vals.get(f, 0.5) for f in _knn_features]]
        xs = _knn_scaler.transform(x)
        probs = dict(zip(_knn_model.classes_, _knn_model.predict_proba(xs)[0]))
        best = max(probs, key=probs.get)
        confidence = round(probs[best], 3)
    else:
        best = min(MOOD_META, key=lambda m: (((valence - MOOD_META[m]["target"]["valence"]) * 2.5) ** 2 + ((energy - MOOD_META[m]["target"]["energy"]) * 2.0) ** 2 + ((tempo - MOOD_META[m]["target"]["tempo"]) / 120) ** 2))
        confidence = 0.75
    return jsonify({"mood": best, "confidence": confidence})


@app.route("/api/ai_chat", methods=["POST", "OPTIONS"])
def ai_chat():
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(silent=True) or {}
    mood = str(data.get("mood", "healing")).lower().strip()
    question = str(data.get("question", "")).strip()
    track = data.get("track")
    lang = str(data.get("lang", "id"))
    if not question:
        return jsonify({"error": "question is required"}), 400
    if mood not in MOOD_META:
        mood = "healing"
    meta = MOOD_META[mood]
    track_ctx = ""
    if track:
        track_ctx = f'\nThe user is currently listening to "{track.get("title", "")}" by {track.get("artist", "")} ({track.get("genre", "")}, valence: {track.get("valence", 0.5)}, energy: {track.get("energy", 0.5)}).'
    system_prompt = f"""You are MoodBeat AI, a compassionate and emotionally intelligent music-therapy companion.

Context:
- User's current mood: "{mood}" ({meta['emoji']}) — {meta['description']}
- Mood vibe: {meta['vibe']}{track_ctx}
- Journal prompt for this mood: "{meta['journal_prompt']}"
- Suggested activity: "{meta['activity']}"

Your response rules:
1. Directly and specifically address what the user wrote, never give a generic answer.
2. Weave in the current song or mood context naturally when relevant.
3. Be concise, warm, and conversational.
4. Offer one gentle, concrete, actionable suggestion tailored to what the user shared.
5. Do not use bullet points, headers, or lists.
6. Respond in {"Bahasa Indonesia" if lang == "id" else "English"}.
7. Never start with "I" or "As an AI". Start with empathy or insight about what the user shared."""
    ai_text = call_external_ai(system_prompt, question, max_tokens=200)
    if not ai_text:
        ai_text = local_ai_response(mood, question, track)
    return jsonify({"response": ai_text, "mood": mood, "source": "external" if ai_text and EXTERNAL_AI_KEY else "local"})


@app.route("/api/journal/export", methods=["POST", "OPTIONS"])
def journal_export():
    if request.method == "OPTIONS":
        return "", 204
    data = request.get_json(silent=True) or {}
    mood = str(data.get("mood", "healing")).lower().strip()
    text = str(data.get("text", "")).strip()
    track = data.get("track")
    if not text:
        return jsonify({"error": "text is required"}), 400
    if mood not in MOOD_META:
        mood = "healing"
    now = datetime.datetime.now()
    date_str = now.strftime("%A, %d %B %Y")
    time_str = now.strftime("%H:%M")
    song_info = f'{track["title"]} — {track["artist"]}' if track else "(no song selected)"
    rtf_bytes = build_rtf(mood, text, song_info, date_str, time_str)
    fname = f"MoodBeat_Journal_{mood}_{now.strftime('%Y-%m-%d')}.rtf"
    return send_file(io.BytesIO(rtf_bytes), mimetype="application/rtf", as_attachment=True, download_name=fname)


@app.route("/", methods=["GET"])
def index():
    html_path = os.path.join(BASE, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read(), 200, {"Content-Type": "text/html; charset=utf-8"}
    return jsonify({"status": "MoodBeat API running", "version": "2.0"}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "knn_ready": HAS_SKLEARN and _knn_model is not None, "ai_ready": bool(EXTERNAL_AI_KEY), "songs": len(load_songs()), "moods": list(MOOD_META.keys())})


if __name__ == "__main__":
    print("=" * 55)
    print("  MoodBeat API v2.1  →  http://localhost:5000")
    print("=" * 55)
    print(f"  Songs loaded : {len(load_songs())}")
    print(f"  KNN model    : {'✓' if HAS_SKLEARN and _knn_model is not None else '✗ (scikit-learn missing)'}")
    print(f"  AI backend   : {'✓' if EXTERNAL_AI_KEY else 'local fallback only'}")
    print("=" * 55)
    app.run(debug=True, port=5000)
