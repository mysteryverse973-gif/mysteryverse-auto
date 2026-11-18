
import os
import json
import asyncio
import subprocess
import random
import time
import requests
import edge_tts

from PIL import Image, ImageDraw, ImageFont
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

VIDEO = "mysteryverse_short.mp4"
POSTER = "poster.png"
AUDIO = "narration.mp3"


def clean_metadata(story):
    raw_tags = story.get("tags", [])
    if not isinstance(raw_tags, list):
        raw_tags = []

    tags = []
    for item in raw_tags:
        if isinstance(item, str):
            tag = " ".join(item.replace("#", "").split()).strip()
            if tag and tag.lower() not in [x.lower() for x in tags]:
                tags.append(tag[:40])

    fallback_tags = [
        "mystery story",
        "mystery shorts",
        "short mystery",
        "fictional mystery",
        "mystery storytelling",
        "short stories",
        "MysteryVerse"
    ]

    for tag in fallback_tags:
        if tag.lower() not in [x.lower() for x in tags]:
            tags.append(tag)

    final_tags = []
    total_chars = 0

    for tag in tags:
        extra = len(tag) + (1 if final_tags else 0)
        if total_chars + extra <= 500:
            final_tags.append(tag)
            total_chars += extra

    story["tags"] = final_tags

    raw_hashtags = story.get("hashtags", [])
    if not isinstance(raw_hashtags, list):
        raw_hashtags = []

    hashtags = []

    for item in raw_hashtags:
        if isinstance(item, str):
            value = "".join(
                ch for ch in item.replace("#", "")
                if ch.isalnum() or ch == "_"
            )
            if value and value.lower() not in [x.lower() for x in hashtags]:
                hashtags.append(value[:30])

    for item in ["MysteryVerse", "Mystery", "Shorts"]:
        if item.lower() not in [x.lower() for x in hashtags]:
            hashtags.append(item)

    story["hashtags"] = hashtags[:5]

    return story


def generate_story():
    key = os.environ["GEMINI_API_KEY"]

    prompt = """
Create one original fictional mystery story
for an English YouTube Short.

Return valid JSON only with these keys:
title, narration, description, tags, hashtags.

Rules:
- Title: compelling English title under 80 characters.
- Narration: 70-90 spoken English words with a strong hook.
- Description: 2-4 natural English sentences about the story.
- Never claim the fictional story is true.
- Tags: array of 8-15 relevant YouTube search tags.
- Hashtags: array of 3-5 relevant hashtags.
- Do not include # symbols inside tags or hashtags.
- Avoid unrelated trending keywords.
- Keep the story family-friendly.
- Do not use real people's names.
"""

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-3.5-flash-lite:generateContent"
    )

    for attempt in range(4):
        try:
            print(f"Generating story (attempt {attempt + 1}/4)...")

            response = requests.post(
                url,
                params={"key": key},
                json={
                    "contents": [
                        {"parts": [{"text": prompt}]}
                    ],
                    "generationConfig": {
                        "responseMimeType": "application/json"
                    }
                },
                timeout=(20, 300)
            )

            if response.status_code in (429, 500, 502, 503, 504):
                if attempt < 3:
                    wait = 15 * (attempt + 1)
                    print(f"Gemini busy. Retrying in {wait}s...")
                    time.sleep(wait)
                    continue

            response.raise_for_status()

            result = response.json()
            candidates = result.get("candidates", [])

            if not candidates:
                raise RuntimeError("Gemini returned no story.")

            story_text = candidates[0]["content"]["parts"][0]["text"]
            story = json.loads(story_text)

            for field in ("title", "narration", "description"):
                if not isinstance(story.get(field), str) or not story[field].strip():
                    raise ValueError(f"Missing field: {field}")

            story = clean_metadata(story)

            print("Story generated successfully.")
            print("SEO tags:", len(story["tags"]))
            print("Hashtags:", len(story["hashtags"]))

            return story

        except (
            requests.exceptions.Timeout,
            requests.exceptions.ConnectionError
        ):
            if attempt == 3:
                raise

            wait = 15 * (attempt + 1)
            print(f"Connection problem. Retrying in {wait}s...")
            time.sleep(wait)

    raise RuntimeError("Gemini failed after 4 attempts.")


def make_poster(title, narration):
    width, height = 1080, 1920
    img = Image.new("RGB", (width, height))
    pixels = img.load()

    for y in range(height):
        shade = int(12 + 30 * y / height)
        for x in range(width):
            pixels[x, y] = (shade, shade, shade + 28)

    draw = ImageDraw.Draw(img)
    random.seed(12)

    for _ in range(100):
        x = random.randint(0, width)
        y = random.randint(0, height)
        draw.ellipse(
            (x, y, x + 4, y + 4),
            fill=(150, 170, 210)
        )

    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    regular_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    try:
        big = ImageFont.truetype(font_path, 76)
        medium = ImageFont.truetype(regular_path, 39)
        small = ImageFont.truetype(font_path, 34)
    except OSError:
        big = medium = small = ImageFont.load_default()

    draw.text(
        (65, 100),
        "MYSTERYVERSE",
        font=small,
        fill="white"
    )

    draw.text(
        (65, 250),
        "TONIGHT'S",
        font=small,
        fill=(120, 190, 255)
    )

    lines = []
    line = ""

    for word in title.split():
        test = (line + " " + word).strip()

        if draw.textbbox((0, 0), test, font=big)[2] > 930:
            if line:
                lines.append(line)
            line = word
        else:
            line = test

    if line:
        lines.append(line)

    y = 350

    for line in lines[:4]:
        draw.text(
            (65, y),
            line,
            font=big,
            fill="white"
        )
        y += 105

    draw.rounded_rectangle(
        (55, 900, 1025, 1550),
        radius=35,
        fill=(22, 30, 55)
    )

    lines = []
    line = ""

    for word in narration[:260].split():
        test = (line + " " + word).strip()

        if draw.textbbox((0, 0), test, font=medium)[2] > 870:
            if line:
                lines.append(line)
            line = word
        else:
            line = test

    if line:
        lines.append(line)

    y = 970

    for line in lines[:8]:
        draw.text(
            (100, y),
            line,
            font=medium,
            fill="white"
        )
        y += 65

    draw.text(
        (65, 1700),
        "LISTEN CLOSELY - THE CLUE IS HIDDEN",
        font=small,
        fill=(120, 190, 255)
    )

    img.save(POSTER)
    print("Poster created.")


async def make_voice(text):
    voice = edge_tts.Communicate(
        text,
        "en-US-AndrewNeural"
    )

    await voice.save(AUDIO)
    print("English narration created.")


def make_video():
    command = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-framerate", "30",
        "-i", POSTER,
        "-i", AUDIO,
        "-vf",
        "scale=1080:1920,zoompan=z='min(zoom+0.0004,1.08)':d=1:s=1080x1920:fps=30,format=yuv420p",
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        "-movflags", "+faststart",
        VIDEO
    ]

    subprocess.run(command, check=True)
    print("Video created successfully.")


def upload_video(story):
    credentials = Credentials(
        token=None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"]
    )

    credentials.refresh(Request())

    youtube = build(
        "youtube",
        "v3",
        credentials=credentials,
        cache_discovery=False
    )

    hashtags = " ".join(
        "#" + h for h in story.get(
            "hashtags",
            ["MysteryVerse", "Mystery", "Shorts"]
        )
    )

    description = (
        story["description"].strip()
        + "\n\nFollow MysteryVerse for more original mystery stories."
        + "\n\n"
        + hashtags
    )[:5000]

    body = {
        "snippet": {
            "title": story["title"][:100],
            "description": description,
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

    print("Private video uploaded successfully!")
    print("YouTube video ID:", result["id"])


if __name__ == "__main__":
    print("Starting MysteryVerse automation...")

    story = generate_story()
    make_poster(story["title"], story["narration"])
    asyncio.run(make_voice(story["narration"]))
    make_video()
    upload_video(story)

    print("MysteryVerse automation completed!")
    
