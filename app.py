import os, json, uuid, asyncio, threading, requests
from flask import Flask, render_template, request, jsonify, send_from_directory
import edge_tts
from moviepy.editor import ImageClip, AudioFileClip, concatenate_videoclips

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
PIXABAY_API_KEY = os.environ.get("PIXABAY_API_KEY", "")

VOICE = "hi-IN-MadhurNeural"
GEMINI_MODEL = "gemini-2.0-flash"

app = Flask(__name__)
OUTPUT = "output"
os.makedirs(OUTPUT, exist_ok=True)
jobs = {}


def write_script(topic, scenes_count):
    prompt = (
        f"टॉपिक: {topic}\n\n"
        f"इस टॉपिक पर {scenes_count} सीन का एक रोचक वीडियो स्क्रिप्ट हिंदी में लिखो। "
        "हर सीन में 2 से 3 छोटे वाक्यों की नैरेशन हो। "
        "सिर्फ़ JSON लौटाओ, इस रूप में: "
        '[{"narration": "हिंदी नैरेशन", "search": "English keywords for stock photo"}] '
        "JSON के अलावा कुछ और मत लिखो।"
    )
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent")
    r = requests.post(
        url,
        headers={"x-goog-api-key": GEMINI_API_KEY,
                 "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=90,
    )
    r.raise_for_status()
    text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def fetch_image(query, path):
    def search(q):
        r = requests.get(
            "https://pixabay.com/api/",
            params={"key": PIXABAY_API_KEY, "q": q, "image_type": "photo",
                    "orientation": "horizontal", "per_page": 5,
                    "safesearch": "true"},
            timeout=30,
        )
        return r.json().get("hits", [])

    hits = search(query) or search("nature")
    url = hits[0]["largeImageURL"]
    with open(path, "wb") as f:
        f.write(requests.get(url, timeout=60).content)


async def make_voice(text, path):
    await edge_tts.Communicate(text, VOICE).save(path)


def build_video(job_id, topic, scenes_count):
    try:
        folder = os.path.join(OUTPUT, job_id)
        os.makedirs(folder, exist_ok=True)

        jobs[job_id].update(status="working", message="स्क्रिप्ट लिखी जा रही है...")
        scenes = write_script(topic, scenes_count)

        clips = []
        for i, sc in enumerate(scenes):
            jobs[job_id]["message"] = f"सीन {i+1} / {len(scenes)} बन रहा है..."
            img = os.path.join(folder, f"s{i}.jpg")
            aud = os.path.join(folder, f"s{i}.mp3")
            fetch_image(sc["search"], img)
            asyncio.run(make_voice(sc["narration"], aud))
            audio = AudioFileClip(aud)
            clip = (ImageClip(img).set_duration(audio.duration + 0.4)
                    .resize(height=480).set_audio(audio))
            clips.append(clip)

        jobs[job_id]["message"] = "सब कुछ जोड़कर वीडियो बन रहा है..."
        final = concatenate_videoclips(clips, method="compose")
        out = os.path.join(folder, "video.mp4")
        final.write_videofile(out, fps=15, codec="libx264",
                              audio_codec="aac", logger=None)
        jobs[job_id].update(status="done", message="वीडियो तैयार है!",
                            video=f"/video/{job_id}", script=scenes)
    except Exception as e:
        jobs[job_id].update(status="error", message=f"गड़बड़ हुई: {e}")


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start():
    data = request.json
    topic = data.get("topic", "").strip()
    scenes = int(data.get("scenes", 4))
    if not topic:
        return jsonify(error="टॉपिक खाली है"), 400
    job_id = uuid.uuid4().hex[:8]
    jobs[job_id] = {"status": "queued", "message": "शुरू हो रहा है..."}
    threading.Thread(target=build_video, args=(job_id, topic, scenes)).start()
    return jsonify(job_id=job_id)


@app.route("/status/<job_id>")
def status(job_id):
    return jsonify(jobs.get(job_id, {"status": "error", "message": "जॉब नहीं मिला"}))


@app.route("/video/<job_id>")
def video(job_id):
    return send_from_directory(os.path.join(OUTPUT, job_id), "video.mp4")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
