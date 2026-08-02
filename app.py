import os
import uuid
import json
import shutil
import subprocess
import threading
import tempfile
from pathlib import Path

from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
from openai import OpenAI
import requests

app = Flask(__name__)

# ALLOWED_ORIGIN doit être l'URL de ton site Netlify, ex: https://ton-site.netlify.app
# Tu peux mettre plusieurs origines séparées par des virgules, ou "*" en dev.
CORS(app, origins=os.environ.get("ALLOWED_ORIGIN", "*").split(","))

# --- Limites anti-dérapage de facturation ---
# Durée max d'une vidéo, en secondes (défaut : 5 minutes)
MAX_VIDEO_DURATION_SECONDS = int(os.environ.get("MAX_VIDEO_DURATION_SECONDS", 300))
# Taille max du fichier uploadé, en Mo (défaut : 200 Mo)
MAX_UPLOAD_SIZE_MB = int(os.environ.get("MAX_UPLOAD_SIZE_MB", 200))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE_MB * 1024 * 1024

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
ELEVENLABS_API_KEY = os.environ["ELEVENLABS_API_KEY"]
ELEVENLABS_VOICE_ID = os.environ.get("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # "Rachel" par défaut

client = OpenAI(api_key=OPENAI_API_KEY)

BASE_DIR = Path(tempfile.gettempdir()) / "video_translate_jobs"
BASE_DIR.mkdir(exist_ok=True)

# Stockage en mémoire des jobs (simple pour un MVP ; se réinitialise si le serveur redémarre)
jobs = {}

LANG_NAMES = {
    "auto": "the same language as the source audio", "fr": "French", "en": "English",
    "es": "Spanish", "de": "German", "it": "Italian", "pt": "Portuguese", "ru": "Russian",
    "ja": "Japanese", "zh": "Chinese", "ar": "Arabic", "hi": "Hindi",
}


def update_job(job_id, **kwargs):
    jobs[job_id].update(kwargs)


def get_video_duration_seconds(video_path):
    """Lit la durée d'une vidéo via ffprobe, sans la traiter."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(video_path),
        ],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def process_video(job_id, video_path, source_lang, target_lang):
    job_dir = video_path.parent
    try:
        # 1. Extraction de l'audio
        update_job(job_id, status="processing", progress=10, message="Extraction de l'audio...")
        audio_path = job_dir / "original_audio.mp3"
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-acodec", "libmp3lame", str(audio_path)],
            check=True, capture_output=True,
        )

        # 2. Transcription (Whisper)
        update_job(job_id, progress=25, message="Transcription en cours (Whisper)...")
        with open(audio_path, "rb") as f:
            transcript_kwargs = {"model": "whisper-1", "file": f}
            if source_lang != "auto":
                transcript_kwargs["language"] = source_lang
            transcript = client.audio.transcriptions.create(**transcript_kwargs)
        original_text = transcript.text

        # 3. Traduction (GPT)
        update_job(job_id, progress=45, message="Traduction du texte...")
        target_name = LANG_NAMES.get(target_lang, target_lang)
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"Translate the given text into {target_name}. Return ONLY the translation, no notes or explanations."},
                {"role": "user", "content": original_text},
            ],
        )
        translated_text = completion.choices[0].message.content.strip()

        # 4. Synthèse vocale (ElevenLabs)
        update_job(job_id, progress=65, message="Génération de la voix (ElevenLabs)...")
        tts_path = job_dir / "translated_audio.mp3"
        resp = requests.post(
            f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}",
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json={
                "text": translated_text,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            },
            timeout=120,
        )
        resp.raise_for_status()
        tts_path.write_bytes(resp.content)

        # 5. Fusion de la nouvelle piste audio avec la vidéo originale
        update_job(job_id, progress=85, message="Fusion avec la vidéo...")
        output_path = job_dir / "output.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(video_path), "-i", str(tts_path),
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-shortest",
                str(output_path),
            ],
            check=True, capture_output=True,
        )

        update_job(job_id, status="completed", progress=100, message="Terminé !", output_path=str(output_path))

    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode(errors="ignore")[:400] if e.stderr else str(e)
        update_job(job_id, status="failed", error=f"Erreur ffmpeg: {stderr}")
    except Exception as e:
        update_job(job_id, status="failed", error=str(e))


@app.route("/api/translate", methods=["POST"])
def translate():
    if "video" not in request.files:
        return jsonify({"error": "Aucun fichier vidéo fourni."}), 400

    video_file = request.files["video"]
    source_lang = request.form.get("source_lang", "auto")
    target_lang = request.form.get("target_lang", "en")

    job_id = str(uuid.uuid4())
    job_dir = BASE_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    video_path = job_dir / f"input{Path(video_file.filename).suffix or '.mp4'}"
    video_file.save(video_path)

    # Vérification de la durée AVANT de dépenser le moindre crédit API
    try:
        duration = get_video_duration_seconds(video_path)
    except (subprocess.CalledProcessError, KeyError, ValueError):
        shutil.rmtree(job_dir, ignore_errors=True)
        return jsonify({"error": "Impossible de lire ce fichier vidéo. Vérifiez le format."}), 400

    if duration > MAX_VIDEO_DURATION_SECONDS:
        shutil.rmtree(job_dir, ignore_errors=True)
        max_minutes = MAX_VIDEO_DURATION_SECONDS // 60
        return jsonify({
            "error": f"Vidéo trop longue ({duration/60:.1f} min). Limite actuelle : {max_minutes} min."
        }), 400

    jobs[job_id] = {"status": "queued", "progress": 0, "message": "En attente...", "output_path": None, "error": None}

    thread = threading.Thread(target=process_video, args=(job_id, video_path, source_lang, target_lang), daemon=True)
    thread.start()

    return jsonify({"job_id": job_id})


@app.route("/api/status/<job_id>", methods=["GET"])
def status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job introuvable."}), 404
    return jsonify({
        "status": job["status"],
        "progress": job["progress"],
        "message": job.get("message", ""),
        "error": job.get("error"),
    })


@app.route("/api/download/<job_id>", methods=["GET"])
def download(job_id):
    job = jobs.get(job_id)
    if not job or job["status"] != "completed":
        return jsonify({"error": "Fichier non disponible."}), 404
    return send_file(job["output_path"], as_attachment=True, download_name="video_traduite.mp4")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
