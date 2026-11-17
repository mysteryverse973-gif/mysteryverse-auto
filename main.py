import os
import json
import asyncio
import subprocess
import random
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

def generate_story():
    key = os.environ["GEMINI_API_KEY"]

    prompt = """
Create one original, fictional, family-friendly mystery story
for an English YouTube Short.
Narration must be 70-90 words, with a strong opening hook.
Do not use real people's names or present fiction as fact.
Return JSON only with these keys:
title, narration, description.
Keep title under 80 characters.
"""

    url = (
        "https://generativelanguage.googleapis.com/v1beta/"
        "models/gemini-3.8-flash:generateContent"
    )

    response = requests.post(
        url,
        params={"key": key},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json"
            }
        },
        timeout=120
    )
    response.raise_for_status()

    result = response.json()
    text = result["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


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
        draw.ellipse((x, y, x + 4, y + 4), fill=(150, 170, 210))

    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    regular_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    try:
        big = ImageFont.truetype(font_path, 76)
        medium = ImageFont.truetype(regular_path, 39)
        small = ImageFont.truetype(font_path, 34)
    except OSError:
        big = medium = small = ImageFont.load_default()

    draw.text((65, 100), "MYSTERYVERSE", font=small, fill="white")
    draw.text((65, 250), "TONIGHT'S", font=small, fill=(120, 190, 255))

    words = title.split()
    lines, line = [], ""

    for word in words:
        test = (line + " " + word).strip()
        if draw.textbbox((0, 0), test, font=big)[2] > 930:
            lines.append(line)
            line = word
        else:
            line = test

    if line:
        lines.append(line)

    y = 350
    for line in lines[:4]:
        draw.text((65, y), line, font=big, fill="white")
        y += 105

    draw.rounded_rectangle(
        (55, 900, 1025, 1550),
        radius=35,
        fill=(22, 30, 55)
    )

    excerpt = narration[:260]
    words = excerpt.split()
    lines, line = [], ""

    for word in words:
        test = (line + " " + word).strip()
        if draw.textbbox((0, 0), test, font=medium)[2] > 870:
            lines.append(line)
            line = word
        else:
            line = test

    if line:
        lines.append(line)

    y = 970
    for line in lines[:8]:
        draw.text((100, y), line, font=medium, fill="white")
        y += 65

    draw.text(
        (65, 1700),
        "LISTEN CLOSELY • THE CLUE IS HIDDEN",
        font=small,
        fill=(120, 190, 255)
    )

    img.save(POSTER)


async def make_voice(text):
    voice = edge_tts.Communicate(
        text,
        "en-US-AndrewNeural"
    )
    await voice.save(AUDIO)


def make_video():
    command = [
        "ffmpeg", "-y",
        "-loop", "1", "-framerate", "30",
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

    body = {
        "snippet": {
            "title": story["title"][:100],
            "description": story["description"] + "\n#MysteryVerse #Shorts",
            "categoryId": "24",
            "defaultLanguage": "en"
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

    print("Private video uploaded:", result["id"])


if __name__ == "__main__":
    story = generate_story()
    make_poster(story["title"], story["narration"])
    asyncio.run(make_voice(story["narration"]))
    make_video()
    upload_video(story)
  
