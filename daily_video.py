#!/usr/bin/env python3
"""
Pipeline YouTube Faceless — Finance Personnelle FR
Génère et publie une vidéo par jour, 100% autonome.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from PIL import Image, ImageDraw, ImageFont

# ── Config ───────────────────────────────────────────────────────────────────

CHANNEL_NAME = "Finance Futée"
TOPICS_FILE = Path("topics_done.json")
PARIS_TZ = timezone(timedelta(hours=2))  # CEST; passer à +1 en hiver si besoin

LOG_FMT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FMT)
log = logging.getLogger("pipeline")

# ── Helpers ──────────────────────────────────────────────────────────────────


def env(key: str) -> str:
    val = os.environ.get(key)
    if not val:
        log.error("Variable d'environnement manquante : %s", key)
        sys.exit(1)
    return val


def load_topics_done() -> list[str]:
    if TOPICS_FILE.exists():
        return json.loads(TOPICS_FILE.read_text())
    return []


def save_topics_done(topics: list[str]) -> None:
    TOPICS_FILE.write_text(json.dumps(topics, ensure_ascii=False, indent=2))


# ── Étape 1 : Génération du script vidéo via Claude ─────────────────────────

SYSTEM_PROMPT = textwrap.dedent("""\
Tu es un créateur de contenu YouTube spécialisé en finance personnelle en France.
Tu produis des scripts pour des vidéos faceless (voix off + texte à l'écran).

Ton style :
- Conversationnel, engageant, comme si tu parlais à un ami
- Des exemples concrets chiffrés (€)
- Pas de jargon inutile, accessible à tous
- Structure claire avec des sections titrées
- Appels à l'action (like, abonne-toi, commente)

IMPORTANT : tu ne dois JAMAIS reprendre un sujet déjà traité.
""")


def generate_script(topics_done: list[str]) -> dict:
    """Retourne {title, description, tags, sections: [{heading, text}]}."""

    already = "\n".join(f"- {t}" for t in topics_done[-50:]) or "(aucun)"

    user_prompt = textwrap.dedent(f"""\
    Génère un script vidéo YouTube finance personnelle FR.
    
    Sujets DÉJÀ traités (ne pas répéter) :
    {already}
    
    Réponds UNIQUEMENT en JSON valide (pas de markdown) avec cette structure :
    {{
      "topic": "résumé court du sujet (pour le tracking)",
      "title": "Titre YouTube accrocheur (max 70 car.)",
      "description": "Description YouTube avec hashtags (3-5 lignes)",
      "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"],
      "sections": [
        {{"heading": "Titre section", "text": "Paragraphe voix-off (150-200 mots)"}},
        ...
      ]
    }}
    
    Le script complet (toutes sections) doit faire 800-1000 mots.
    Génère 5-6 sections.
    """)

    log.info("Appel Claude pour génération du script…")
    r = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": env("ANTHROPIC_API_KEY"),
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4096,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_prompt}],
        },
        timeout=120,
    )
    r.raise_for_status()
    text = r.json()["content"][0]["text"]

    # Nettoyage éventuel de backticks markdown
    text = re.sub(r"^```json\s*", "", text.strip())
    text = re.sub(r"\s*```$", "", text.strip())

    data = json.loads(text)
    log.info("Script généré — sujet : %s", data["topic"])
    return data


# ── Étape 2 : Voix off ElevenLabs ───────────────────────────────────────────


def generate_voiceover(script_data: dict, out_path: Path) -> None:
    """Génère le MP3 de la voix off."""
    full_text = "\n\n".join(
        f"{s['heading']}.\n{s['text']}" for s in script_data["sections"]
    )

    log.info("Appel ElevenLabs TTS (%d caractères)…", len(full_text))
    voice_id = env("ELEVENLABS_VOICE_ID")
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        headers={
            "xi-api-key": env("ELEVENLABS_API_KEY"),
            "content-type": "application/json",
        },
        json={
            "text": full_text,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        timeout=300,
    )
    r.raise_for_status()
    out_path.write_bytes(r.content)
    log.info("MP3 généré : %s (%.1f Mo)", out_path, out_path.stat().st_size / 1e6)


# ── Étape 3 : Vidéo FFmpeg ──────────────────────────────────────────────────


def get_audio_duration(mp3: Path) -> float:
    """Durée en secondes via ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(mp3),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def build_subtitle_text(script_data: dict) -> str:
    """Construit un fichier ASS basique avec les phrases réparties dans le temps."""
    sections = script_data["sections"]
    all_phrases = []
    for sec in sections:
        # Découpe le texte en phrases
        sentences = re.split(r'(?<=[.!?])\s+', sec["text"].strip())
        all_phrases.append({"heading": sec["heading"], "sentences": sentences})
    return all_phrases


def generate_video(script_data: dict, mp3: Path, mp4: Path) -> None:
    """Crée la vidéo avec FFmpeg : fond sombre + sous-titres brûlés."""

    duration = get_audio_duration(mp3)
    log.info("Durée audio : %.1f s", duration)

    # Construire toutes les phrases à afficher
    all_sentences = []
    for sec in script_data["sections"]:
        all_sentences.append(sec["heading"].upper())
        for s in re.split(r'(?<=[.!?])\s+', sec["text"].strip()):
            if s.strip():
                all_sentences.append(s.strip())

    n = len(all_sentences)
    time_per = duration / n if n else 5

    # Créer fichier ASS pour sous-titres style Hormozi
    ass_content = textwrap.dedent("""\
    [Script Info]
    ScriptType: v4.00+
    PlayResX: 1920
    PlayResY: 1080

    [V4+ Styles]
    Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
    Style: Heading,Arial,80,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,60,60,80,1
    Style: Default,Arial,60,&H00FFFFFF,&H000000FF,&H00111111,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,60,60,80,1
    Style: Accent,Arial,64,&H0000D4FF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,2,2,60,60,80,1

    [Events]
    Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text
    """)

    def fmt_time(seconds: float) -> str:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = seconds % 60
        return f"{h}:{m:02d}:{s:05.2f}"

    for i, phrase in enumerate(all_sentences):
        start = i * time_per
        end = (i + 1) * time_per
        # Les headings en majuscules => style Heading
        style = "Heading" if phrase == phrase.upper() and len(phrase) < 60 else "Default"
        # Wrap long lines
        if len(phrase) > 50:
            mid = len(phrase) // 2
            # Chercher un espace proche du milieu
            space = phrase.rfind(" ", 0, mid + 10)
            if space > mid - 15:
                phrase = phrase[:space] + "\\N" + phrase[space + 1:]
        escaped = phrase.replace("\n", "\\N")
        ass_content += f"Dialogue: 0,{fmt_time(start)},{fmt_time(end)},{style},,0,0,0,,{escaped}\n"

    # Écrire le fichier ASS
    ass_file = mp3.parent / "subs.ass"
    ass_file.write_text(ass_content, encoding="utf-8")

    # Commande FFmpeg
    # Fond gradient sombre via lavfi
    cmd = [
        "ffmpeg", "-y",
        # Fond noir/bleu foncé
        "-f", "lavfi", "-i",
        f"color=c=0x0a0e27:s=1920x1080:d={duration},format=yuv420p",
        # Audio
        "-i", str(mp3),
        # Sous-titres brûlés + watermark texte
        "-vf", (
            f"ass={ass_file},"
            f"drawtext=text='{CHANNEL_NAME}':fontsize=28:fontcolor=white@0.4"
            f":x=w-tw-40:y=h-th-30"
        ),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        str(mp4),
    ]

    log.info("Rendu vidéo FFmpeg…")
    subprocess.run(cmd, check=True, capture_output=True)
    log.info("Vidéo générée : %s (%.1f Mo)", mp4, mp4.stat().st_size / 1e6)


# ── Thumbnail ────────────────────────────────────────────────────────────────


def generate_thumbnail(title: str, out_path: Path) -> None:
    """Génère une miniature 1280x720 avec Pillow."""
    img = Image.new("RGB", (1280, 720), color=(10, 14, 39))
    draw = ImageDraw.Draw(img)

    # Gradient overlay (bande dorée en bas)
    for y in range(500, 720):
        alpha = int(200 * (y - 500) / 220)
        draw.line([(0, y), (1280, y)], fill=(255, 180, 0, alpha))

    # Titre — on utilise la font par défaut (dispo partout)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
    except OSError:
        font = ImageFont.load_default()
        font_sm = font

    # Wrap title
    words = title.split()
    lines, current = [], ""
    for w in words:
        test = f"{current} {w}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] > 1100:
            lines.append(current)
            current = w
        else:
            current = test
    if current:
        lines.append(current)

    y_start = 200 - len(lines) * 40
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        x = (1280 - bbox[2]) // 2
        # Ombre
        draw.text((x + 3, y_start + i * 85 + 3), line, font=font, fill=(0, 0, 0))
        draw.text((x, y_start + i * 85), line, font=font, fill=(255, 255, 255))

    # Nom de chaîne
    draw.text((40, 650), CHANNEL_NAME, font=font_sm, fill=(255, 200, 50))

    img.save(str(out_path), quality=95)
    log.info("Thumbnail générée : %s", out_path)


# ── Étape 4 : Upload YouTube ────────────────────────────────────────────────


def upload_to_youtube(
    mp4: Path, thumb: Path, title: str, description: str, tags: list[str]
) -> str:
    """Upload la vidéo et retourne l'ID YouTube."""

    creds = Credentials(
        token=None,
        refresh_token=env("YOUTUBE_REFRESH_TOKEN"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=env("YOUTUBE_CLIENT_ID"),
        client_secret=env("YOUTUBE_CLIENT_SECRET"),
    )

    youtube = build("youtube", "v3", credentials=creds)

    # Programmer la publication à 18h00 Paris aujourd'hui
    publish_at = (
        datetime.now(PARIS_TZ)
        .replace(hour=18, minute=0, second=0, microsecond=0)
        .isoformat()
    )

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "27",  # Education
            "defaultLanguage": "fr",
        },
        "status": {
            "privacyStatus": "private",
            "publishAt": publish_at,
            "selfDeclaredMadeForKids": False,
        },
    }

    log.info("Upload YouTube : %s", title)
    media = MediaFileUpload(str(mp4), mimetype="video/mp4", resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = req.next_chunk()
        if status:
            log.info("Upload : %.0f%%", status.progress() * 100)

    video_id = response["id"]
    log.info("Vidéo uploadée ! ID : %s", video_id)

    # Thumbnail
    youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(thumb), mimetype="image/jpeg")).execute()
    log.info("Thumbnail uploadée.")

    return video_id


# ── Main ─────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Pipeline YouTube faceless")
    parser.add_argument("--dry-run", action="store_true", help="Tout sauf l'upload YouTube")
    args = parser.parse_args()

    log.info("=== Début du pipeline ===")

    # 1. Script
    topics_done = load_topics_done()
    script_data = generate_script(topics_done)

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        mp3 = tmp / "voiceover.mp3"
        mp4 = tmp / "video.mp4"
        thumb = tmp / "thumbnail.jpg"

        # 2. Voix off
        generate_voiceover(script_data, mp3)

        # 3. Vidéo
        generate_video(script_data, mp3, mp4)

        # 4. Thumbnail
        generate_thumbnail(script_data["title"], thumb)

        # 5. Upload
        if args.dry_run:
            log.info("[DRY RUN] Upload ignoré. Fichiers dans %s", tmp)
            # Copier dans le dossier courant pour inspection
            import shutil
            for f in [mp3, mp4, thumb]:
                shutil.copy(f, Path.cwd() / f.name)
            log.info("Fichiers copiés dans le dossier courant.")
        else:
            upload_to_youtube(
                mp4, thumb,
                script_data["title"],
                script_data["description"],
                script_data["tags"],
            )

    # Tracker le sujet
    topics_done.append(script_data["topic"])
    save_topics_done(topics_done)
    log.info("=== Pipeline terminé ===")


if __name__ == "__main__":
    main()
