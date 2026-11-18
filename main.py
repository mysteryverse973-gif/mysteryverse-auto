
import os
import re
import json
import time
import asyncio
import subprocess
import requests
import edge_tts

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

VIDEO = "mysteryverse_short.mp4"
AUDIO = "narration.mp3"
CLIP_DIR = "clips"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

HEADERS = {
    "User-Agent": "MysteryVerseBot/1.0 (video creator)"
}


def run(cmd):
    subprocess.run(cmd, check=True)


def duration(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path
        ],
        capture_output=True, text=True, check=True
    )
    return float(result.stdout.strip())


def generate_story():
    prompt = """
Create an original fictional mystery story for an English YouTube Short.
Return valid JSON with:
title, narration, description, tags, hashtags.

Rules:
- Narration: 70-90 spoken English words.
- Strong suspense hook in the first sentence.
- Family-friendly fictional mystery.
- Title under 80 characters.
- Description 2-4 sentences.
- tags and hashtags must be lists.
- Never claim the story is real.
"""
    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-3.5-flash-lite:generateContent"
    )

    key = os.environ["GEMINI_API_KEY"]

    for attempt in range(4):
        response = requests.post(
            url,
            params={"key": key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json"
                }
            },
            timeout=180
        )

        if response.status_code in (429, 500, 502, 503, 504):
            time.sleep(15 * (attempt + 1))
            continue

        response.raise_for_status()
        candidates = response.json().get("candidates", [])

        if not candidates:
            raise RuntimeError("No story returned by Gemini.")

        story = json.loads(
            candidates[0]["content"]["parts"][0]["text"]
        )

        for field in ("title", "narration", "description"):
            if not story.get(field):
                raise ValueError("Missing story field: " + field)

        return story

    raise RuntimeError("Story generation failed.")


async def create_voice(text):
    voice = edge_tts.Communicate(
        text, "en-US-AndrewNeural"
    )
    await voice.save(AUDIO)


def search_terms(story):
    words = re.findall(
        r"[a-z]{4,}",
        (story["title"] + " " + story["narration"]).lower()
    )

    ignored = {
        "there", "their", "where", "which", "would",
        "could", "about", "after", "before", "while",
        "never", "every", "because", "someone",
        "something", "inside", "heard", "looked",
        "turned", "found", "story"
    }

    result = []
    for word in words:
        if word not in ignored and word not in result:
            result.append(word)
        if len(result) == 6:
            break

    return result or ["dark forest", "abandoned house"]


def allowed_license(metadata):
    name = metadata.get(
        "LicenseShortName", {}
    ).get("value", "").lower()

    url = metadata.get(
        "LicenseUrl", {}
    ).get("value", "").lower()

    text = name + " " + url

    allowed = (
        "cc0" in text
        or "public domain" in text
        or "cc by" in text
        or "cc-by" in text
    )

    forbidden = any(x in text for x in [
        "by-nc", "by-nd", "noncommercial",
        "no derivatives"
    ])

    return allowed and not forbidden


def search_commons(query):
    params = {
        "action": "query",
        "format": "json",
        "generator": "search",
        "gsrnamespace": 6,
        "gsrlimit": 20,
        "gsrsearch": query,
        "prop": "imageinfo",
        "iiprop": "url|mime|extmetadata"
    }

    response = requests.get(
        COMMONS_API,
        params=params,
        headers=HEADERS,
        timeout=45
    )
    response.raise_for_status()

    pages = response.json().get(
        "query", {}
    ).get("pages", {})

    found = []

    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        metadata = info.get("extmetadata", {})

        if not info.get("mime", "").lower().startswith("video/"):
            continue

        if not allowed_license(metadata):
            continue

        url = info.get("url", "")
        if not url.startswith("https://"):
            continue

        found.append({
            "url": url,
            "title": page.get("title", "Video"),
            "license": metadata.get(
                "LicenseShortName", {}
            ).get("value", "Unknown"),
            "artist": metadata.get(
                "Artist", {}
            ).get("value", "Unknown")
        })

    return found


def download_clip(item, number):
    os.makedirs(CLIP_DIR, exist_ok=True)
    path = os.path.join(CLIP_DIR, f"source_{number}.video")

    response = requests.get(
        item["url"],
        headers=HEADERS,
        stream=True,
        timeout=(30, 120)
    )
    response.raise_for_status()

    total = 0
    with open(path, "wb") as file:
        for chunk in response.iter_content(262144):
            if not chunk:
                continue

            total += len(chunk)
            if total > 60 * 1024 * 1024:
                raise ValueError("Clip too large.")

            file.write(chunk)

    return path


def create_moving_video(story):
    if os.path.exists(CLIP_DIR):
        for name in os.listdir(CLIP_DIR):
            path = os.path.join(CLIP_DIR, name)
            if os.path.isfile(path):
                os.remove(path)

    os.makedirs(CLIP_DIR, exist_ok=True)

    found = []
    seen = set()

    for term in search_terms(story):
        try:
            for item in search_commons(term):
                if item["url"] not in seen:
                    seen.add(item["url"])
                    found.append(item)
        except Exception as error:
            print("Search error:", error)

    segments = []
    credits = []

    for item in found:
        try:
            number = len(segments) + 1
            source = download_clip(item, number)

            segment = os.path.join(
                CLIP_DIR, f"scene_{number}.mp4"
            )

            run([
                "ffmpeg", "-y",
                "-i", source,
                "-t", "8",
                "-an",
                "-vf",
                "scale=1080:1920:"
                "force_original_aspect_ratio=increase,"
                "crop=1080:1920,fps=30,setsar=1,"
                "format=yuv420p",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "24",
                segment
            ])

            if duration(segment) >= 6:
                segments.append(segment)
                credits.append(
                    f'{item["title"]} | {item["license"]} | '
                    f'{item["artist"]} | {item["url"]}'
                )

            if len(segments) >= 8:
                break

        except Exception as error:
            print("Skipping clip:", error)

    # Never upload a poster or a video with too few scenes.
    if len(segments) < 4:
        raise RuntimeError(
            "Upload cancelled: fewer than 4 distinct moving scenes."
        )

    audio_duration = duration(AUDIO)
    total_visual_duration = sum(
        duration(path) for path in segments
    )

    # Never repeat clips to fill the narration.
    if total_visual_duration < audio_duration:
        raise RuntimeError(
            "Upload cancelled: moving scenes are too short "
            "for the narration."
        )

    concat_file = os.path.join(CLIP_DIR, "concat.txt")

    with open(concat_file, "w", encoding="utf-8") as file:
        for path in segments:
            absolute = os.path.abspath(path)
            file.write(
                "file '" + absolute.replace("'", "'\\''") + "'\n"
            )

    visuals = os.path.join(CLIP_DIR, "visuals.mp4")

    run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        visuals
    ])

    run([
        "ffmpeg", "-y",
        "-i", visuals,
        "-i", AUDIO,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-vf",
        "scale=1080:1920:"
        "force_original_aspect_ratio=increase,"
        "crop=1080:1920,fps=30,format=yuv420p",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        VIDEO
    ])

    if duration(VIDEO) < audio_duration * 0.95:
        raise RuntimeError("Final video failed duration check.")

    print("Moving-scene video created.")
    return credits


def upload_to_youtube(story, credits):
    credentials = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=[
            "https://www.googleapis.com/auth/youtube.upload"
        ]
    )

    credentials.refresh(Request())

    youtube = build(
        "youtube", "v3",
        credentials=credentials,
        cache_discovery=False
    )

    hashtags = " ".join(
        "#" + tag for tag in story.get(
            "hashtags", ["MysteryVerse", "Mystery", "Shorts"]
        )
    )

    description = (
        story["description"]
        + "\n\nFollow MysteryVerse for more mystery stories."
        + "\n\n" + hashtags
        + "\n\nFootage credits:\n"
        + "\n".join(credits)
    )

    body = {
        "snippet": {
            "title": story["title"][:100],
            "description": description[:5000],
            "tags": story.get("tags", [])[:15],
            "categoryId": "24",
            "defaultLanguage": "en",
            "defaultAudioLanguage": "en"
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        VIDEO,
        mimetype="video/mp4",
        resumable=True
    )

    result = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    ).execute()

    print("Private upload successful:", result["id"])


if __name__ == "__main__":
    print("Starting MysteryVerse...")

    story = generate_story()
    asyncio.run(create_voice(story["narration"]))

    credits = create_moving_video(story)

    # Upload happens only after all moving-scene checks pass.
    upload_to_youtube(story, credits)

    print("MysteryVerse completed.")
    
