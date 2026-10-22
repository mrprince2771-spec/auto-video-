import os, json, uuid, asyncio, threading, requests
from flask import Flask, render_template, request, jsonify, send_from_directory
import anthropic
import edge_tts
from moviepy.editor import (ImageClip, AudioFileClip, concatenate_videoclips)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PEXELS_API_KEY = os.environ.get("PEXELS_API_KEY", "")

VOICE = "hi-IN-MadhurNeural"   # महिला आवाज़ के लिए: hi-IN-SwaraNeural
MODEL = "claude-sonnet-4-5"

app = Flask(__name__)
client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
OUTPUT = "output"
os.makedirs(OUTPUT, exist_ok=True)
jobs = {}  # job_id -> {status, message, video}


def write_script(topic, scenes_count):
    prompt = f"""टॉपिक: {topic}

इस टॉपिक पर {scenes_count} सीन का एक रोचक वीडियो स्क्रिप्ट हिंदी में लिखो।
हर सीन में 2 से 3 छोटे वाक्यों की नैरेशन हो।
सिर्फ़ JSON लौटाओ, इस रूप में:
[{{"narration": "हिंदी नैरेशन", "search": "English keywords for stock photo"}}]
JSON के अलावा कुछ और मत लिखो।"""
    msg = client.messages.create(
        model=MODEL, max_tokens=3000,
        messages=[{"role": "user", "content": prompt}])
    text = msg.content[0].text.strip()
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def fetch_image(query, path):
    r = requests.get("https://api.pexels.com/v1/search",
                     headers={"Authorization": PEXELS_API_KEY},
                     params={"query": query, "per_page": 1, "orientation": "landscape"},
                     timeout=30)
    photos = r.json().get("photos", [])
    if not photos:
        r = requests.get("https://api.pexels.com/v1/search",
                         headers={"Authorization": PEXELS_API_KEY},
                         params={"query": "abstract background", "per_page": 1},
                         timeout=30)
        photos = r.json()["photos"]
    url = photos[0]["src"]["large2x"]
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
                    .resize(height=720).set_audio(audio))
            clips.append(clip)

        jobs[job_id]["message"] = "सब कुछ जोड़कर वीडियो बन रहा है..."
        final = concatenate_videoclips(clips, method="compose")
        out = os.path.join(folder, "video.mp4")
        final.write_videofile(out, fps=24, codec="libx264",
                              audio_codec="aac", logger=None)
        jobs[job_id].update(status="done", message="वीडियो तैयार है!",
                            video=f"/video/{job_id}",
                            script=scenes)
    except Exception as e:
        jobs[job_id].update(status="error", message=f"गड़बड़ हुई: {e}")


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start():
    data = request.json
    topic = data.get("topic", "").strip()
    scenes = int(data.get("scenes", 5))
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

