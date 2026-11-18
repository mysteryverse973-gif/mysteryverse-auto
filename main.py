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

# =====================================
# MYSTERYVERSE CONFIGURATION
# =====================================

VIDEO = "mysteryverse_short.mp4"
AUDIO = "narration.mp3"
CLIP_DIR = "clips"

HEADERS = {
    "User-Agent": "MysteryVerseBot/1.0 (automated video creator)"
}

MAX_CLIP_SIZE = 60 * 1024 * 1024
MIN_CLIPS = 4
MAX_CLIPS = 8
CLIP_SECONDS = 8


# =====================================
# GENERAL HELPERS
# =====================================

def run(command):
    subprocess.run(command, check=True)


def duration(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path
        ],
        capture_output=True,
        text=True,
        check=True
    )
    return float(result.stdout.strip())


def request_with_retry(method, url, **kwargs):
    for attempt in range(4):
        response = requests.request(
            method,
            url,
            headers=kwargs.pop("headers", HEADERS),
            **kwargs
        )

        if response.status_code in (429, 500, 502, 503, 504):
            wait = 10 * (attempt + 1)
            print(f"Server busy ({response.status_code}). Waiting {wait}s.")
            time.sleep(wait)
            continue

        response.raise_for_status()
        return response

    raise RuntimeError("Request failed after multiple retries: " + url)


# =====================================
# AI STORY GENERATION
# =====================================

def generate_story():
    prompt = """
Create an original fictional mystery story for an English YouTube Short.

Return valid JSON with these fields:
title, narration, description, tags, hashtags.

Rules:
- Narration must contain 70-90 spoken English words.
- Start with a powerful suspense hook.
- Make the story family-friendly.
- Make the ending surprising.
- Title must be under 80 characters.
- Description must be 2-4 sentences.
- tags and hashtags must be lists of strings.
- Never claim the fictional story is real.
"""

    api_key = os.environ["GEMINI_API_KEY"]

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-3.5-flash-lite:generateContent"
    )

    for attempt in range(4):
        response = requests.post(
            url,
            params={"key": api_key},
            json={
                "contents": [
                    {"parts": [{"text": prompt}]}
                ],
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
            raise RuntimeError("Gemini returned no story.")

        raw_text = candidates[0]["content"]["parts"][0]["text"]
        story = json.loads(raw_text)

        for field in ("title", "narration", "description"):
            if not story.get(field):
                raise ValueError("Missing story field: " + field)

        if not isinstance(story.get("tags", []), list):
            story["tags"] = []

        if not isinstance(story.get("hashtags", []), list):
            story["hashtags"] = []

        story["title"] = str(story["title"])[:80]
        story["narration"] = str(story["narration"])
        story["description"] = str(story["description"])

        print("Story generated:", story["title"])
        return story

    raise RuntimeError("Story generation failed after retries.")


# =====================================
# AI VOICE
# =====================================

async def create_voice(text):
    voice = edge_tts.Communicate(
        text,
        "en-US-AndrewNeural"
    )
    await voice.save(AUDIO)

    if not os.path.exists(AUDIO) or os.path.getsize(AUDIO) == 0:
        raise RuntimeError("Voice narration was not created.")

    print("Voice narration created.")


# =====================================
# SEARCH TERMS
# =====================================

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
        "turned", "found", "story", "then", "when",
        "that", "this", "with", "from"
    }

    terms = []

    for word in words:
        if word not in ignored and word not in terms:
            terms.append(word)

        if len(terms) >= 6:
            break

    if not terms:
        terms = ["dark forest", "abandoned house"]

    return terms


# =====================================
# INTERNET ARCHIVE LICENCE CHECK
# Wikimedia is intentionally disabled.
# =====================================

def allowed_archive_license(license_url):
    value = str(license_url or "").lower().strip()

    if not value:
        return False

    forbidden = (
        "by-nc",
        "by-nd",
        "noncommercial",
        "no-derivatives"
    )

    if any(word in value for word in forbidden):
        return False

    allowed = (
        "creativecommons.org/publicdomain/zero",
        "creativecommons.org/publicdomain/mark",
        "creativecommons.org/licenses/by/",
        "creativecommons.org/licenses/by-sa/"
    )

    return any(word in value for word in allowed)


# =====================================
# INTERNET ARCHIVE SEARCH
# =====================================

def search_archive(query):
    params = {
        "q": (
            'mediatype:movies AND '
            f'(title:"{query}" OR subject:"{query}")'
        ),
        "fl[]": ["identifier", "title"],
        "rows": 10,
        "page": 1,
        "output": "json"
    }

    response = request_with_retry(
        "GET",
        "https://archive.org/advancedsearch.php",
        params=params,
        timeout=60
    )

    docs = response.json().get(
        "response", {}
    ).get("docs", [])

    found = []

    for doc in docs:
        identifier = doc.get("identifier")

        if not identifier:
            continue

        try:
            metadata_response = request_with_retry(
                "GET",
                f"https://archive.org/metadata/{identifier}",
                timeout=60
            )

            record = metadata_response.json()
            metadata = record.get("metadata", {})

            license_url = metadata.get("licenseurl", "")

            if isinstance(license_url, list):
                license_url = (
                    license_url[0] if license_url else ""
                )

            if not allowed_archive_license(license_url):
                print("Skipping item with unapproved licence:", identifier)
                continue

            video_files = []

            for entry in record.get("files", []):
                filename = entry.get("name", "")
                lower = filename.lower()
                file_format = str(
                    entry.get("format", "")
                ).lower()

                if not lower.endswith((
                    ".mp4", ".m4v", ".mov", ".webm"
                )):
                    continue

                if "thumbnail" in file_format:
                    continue

                if "animated gif" in file_format:
                    continue

                try:
                    size = int(entry.get("size", 0))
                except (TypeError, ValueError):
                    size = 0

                if size and size > MAX_CLIP_SIZE:
                    continue

                priority = 0 if lower.endswith(".mp4") else 1
                video_files.append((priority, filename))

            if not video_files:
                continue

            video_files.sort(key=lambda item: item[0])

            filename = requests.utils.quote(
                video_files[0][1],
                safe="/"
            )

            creator = metadata.get("creator", "Unknown")

            if isinstance(creator, list):
                creator = ", ".join(str(x) for x in creator)

            found.append({
                "url": (
                    f"https://archive.org/download/"
                    f"{identifier}/{filename}"
                ),
                "title": str(
                    metadata.get(
                        "title",
                        doc.get("title", identifier)
                    )
                ),
                "license": str(license_url),
                "artist": str(creator),
                "source": "Internet Archive"
            })

        except Exception as error:
            print("Archive item skipped:", error)

        time.sleep(1)

    return found


# =====================================
# DOWNLOAD VIDEO CLIPS
# =====================================

def download_clip(item, number):
    os.makedirs(CLIP_DIR, exist_ok=True)

    path = os.path.join(
        CLIP_DIR,
        f"source_{number}.video"
    )

    response = requests.get(
        item["url"],
        headers=HEADERS,
        stream=True,
        timeout=(30, 120)
    )

    response.raise_for_status()

    total = 0

    with open(path, "wb") as output:
        for chunk in response.iter_content(262144):
            if not chunk:
                continue

            total += len(chunk)

            if total > MAX_CLIP_SIZE:
                raise ValueError("Clip exceeded size limit.")

            output.write(chunk)

    if total == 0:
        raise RuntimeError("Downloaded clip is empty.")

    return path


# =====================================
# VIDEO CREATION
# =====================================

def create_moving_video(story):
    if os.path.exists(CLIP_DIR):
        for name in os.listdir(CLIP_DIR):
            path = os.path.join(CLIP_DIR, name)

            if os.path.isfile(path):
                os.remove(path)

    os.makedirs(CLIP_DIR, exist_ok=True)

    found = []
    seen = set()

    # Internet Archive only: no Wikimedia requests.
    for term in search_terms(story):
        try:
            print("Searching Internet Archive:", term)

            for item in search_archive(term):
                if item["url"] not in seen:
                    seen.add(item["url"])
                    found.append(item)

        except Exception as error:
            print("Archive search failed:", error)

        time.sleep(2)

    print("Unique footage candidates:", len(found))

    segments = []
    credits = []

    for item in found:
        try:
            number = len(segments) + 1

            source_path = download_clip(item, number)

            segment = os.path.join(
                CLIP_DIR,
                f"scene_{number}.mp4"
            )

            run([
                "ffmpeg", "-y",
                "-i", source_path,
                "-t", str(CLIP_SECONDS),
                "-an",
                "-vf",
                "scale=1080:1920:"
                "force_original_aspect_ratio=increase,"
                "crop=1080:1920,"
                "fps=30,setsar=1,format=yuv420p",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "24",
                "-movflags", "+faststart",
                segment
            ])

            if duration(segment) >= 6:
                segments.append(segment)

                credits.append(
                    f'{item["title"]} | '
                    f'{item["license"]} | '
                    f'{item["artist"]} | '
                    f'{item["url"]}'
                )

                print("Usable clip:", len(segments))

            else:
                print("Clip too short; skipped.")

            if len(segments) >= MAX_CLIPS:
                break

        except Exception as error:
            print("Skipping footage:", error)

    # Fail safely instead of uploading an incomplete video.
    if len(segments) < MIN_CLIPS:
        raise RuntimeError(
            f"Upload cancelled: only {len(segments)} usable clips. "
            f"At least {MIN_CLIPS} are required."
        )

    audio_duration = duration(AUDIO)

    visual_duration = sum(
        duration(path) for path in segments
    )

    if visual_duration < audio_duration:
        raise RuntimeError(
            "Upload cancelled: footage is shorter than narration."
        )

    concat_file = os.path.join(
        CLIP_DIR,
        "concat.txt"
    )

    with open(
        concat_file,
        "w",
        encoding="utf-8"
    ) as output:
        for path in segments:
            absolute = os.path.abspath(path)

            safe_path = absolute.replace("'", "'\\''")

            output.write(
                f"file '{safe_path}'\n"
            )

    visuals = os.path.join(
        CLIP_DIR,
        "visuals.mp4"
    )

    run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_file,
        "-c", "copy",
        visuals
    ])

    # Combine footage and narration into a vertical Short.
    run([
        "ffmpeg", "-y",
        "-i", visuals,
        "-i", AUDIO,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-vf",
        "scale=1080:1920:"
        "force_original_aspect_ratio=increase,"
        "crop=1080:1920,"
        "fps=30,format=yuv420p",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        VIDEO
    ])

    final_duration = duration(VIDEO)

    if final_duration < audio_duration * 0.95:
        raise RuntimeError(
            "Final video failed duration validation."
        )

    print("Final vertical video created:", final_duration, "seconds")

    return credits


# =====================================
# YOUTUBE PRIVATE UPLOAD
# =====================================

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
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )

    hashtags = " ".join(
        "#" + str(tag).strip().lstrip("#").replace(" ", "")
        for tag in story.get(
            "hashtags",
            ["MysteryVerse", "Mystery", "Shorts"]
        )
    )

    description = (
        story["description"]
        + "\n\nFollow MysteryVerse for more mystery stories."
        + "\n\n" + hashtags
        + "\n\nFootage credits:\n"
        + "\n".join(credits)
    )

    tags = [
        str(tag)[:100]
        for tag in story.get("tags", [])[:15]
    ]

    body = {
        "snippet": {
            "title": story["title"][:100],
            "description": description[:5000],
            "tags": tags,
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

    print("Private YouTube upload successful.")
    print("Video ID:", result["id"])
    print("Video URL:", "https://youtube.com/watch?v=" + result["id"])


# =====================================
# MAIN AUTOMATION
# =====================================

if __name__ == "__main__":
    print("================================")
    print("Starting MysteryVerse Automation")
    print("================================")

    required_secrets = [
        "GEMINI_API_KEY",
        "YOUTUBE_CLIENT_ID",
        "YOUTUBE_CLIENT_SECRET",
        "YOUTUBE_REFRESH_TOKEN"
    ]

    missing = [
        name for name in required_secrets
        if not os.environ.get(name)
    ]

    if missing:
        raise RuntimeError(
            "Missing GitHub Actions secrets: "
            + ", ".join(missing)
        )

    story = generate_story()

    asyncio.run(
        create_voice(story["narration"])
    )

    credits = create_moving_video(story)

    # Upload happens only after all video checks pass.
    upload_to_youtube(story, credits)

    print("================================")
    print("MysteryVerse completed successfully.")
    print("Upload privacy: PRIVATE")
    print("================================")
    
