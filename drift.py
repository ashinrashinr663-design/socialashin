import re
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
from user_agent import generate_user_agent
import yt_dlp

app = Flask(__name__)
CORS(app)

CREDITS = {
    "Developer": "t.me/dg_drift",
    "Main Channel": "@DGDRIFT",
    "Owner": "@DG_DRIFT"
}

def success_resp(data: dict):
    return jsonify({"status": "success", **data, **CREDITS}), 200

def error_resp(message: str, code: int = 400):
    return jsonify({"status": "error", "message": message, **CREDITS}), code

def resolve_redirects(url: str) -> str:
    """Expands shortened URLs (pin.it, t.co, vt.tiktok.com) to canonical URLs."""
    try:
        if any(short in url for short in ["pin.it", "t.co", "vt.tiktok.com", "vm.tiktok.com", "fb.watch"]):
            r = requests.head(url, allow_redirects=True, timeout=10)
            return r.url
    except Exception:
        pass
    return url

def clean_media_url(link: str):
    if not link:
        return None
    link = link.replace("\\u0026", "&").replace("&quot;", "").replace('\\/', '/').strip()
    return link.split(",mediaPreviewUrl:")[0].rstrip("&")

# ============================================================
# EXPERTSTOOL CORE ENGINE
# ============================================================
def extract_via_expertstool(url: str, referer_path: str, action_url: str = "https://www.expertstool.com/converter.php"):
    if not url:
        return error_resp("Video URL is required")

    url = resolve_redirects(url)
    ua = generate_user_agent()
    headers = {
        "authority": "www.expertstool.com",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "content-type": "application/x-www-form-urlencoded",
        "origin": "https://www.expertstool.com",
        "referer": f"https://www.expertstool.com/{referer_path}",
        "user-agent": ua
    }
    payload = {"url": url}

    try:
        res = requests.post(action_url, headers=headers, data=payload, timeout=25)
        html = res.text

        if "API Not Work" in html or "Invalid" in html and "source" not in html:
            return error_resp("Invalid or unsupported media URL for this service", 400)

        soup = BeautifulSoup(html, "html.parser")
        video_links = []
        image_links = []

        # 1. Search in <source src="...">
        for source in soup.find_all("source"):
            src = clean_media_url(source.get("src"))
            if src and src.startswith("http") and src not in video_links:
                video_links.append(src)

        # 2. Search in download buttons/anchor tags
        for a in soup.find_all("a", href=True):
            href = clean_media_url(a["href"])
            if not href or not href.startswith("http"):
                continue
            # Match videos
            if any(ext in href.lower() for ext in [".mp4", "googlevideo", "video_url", "video-"]) and href not in video_links:
                video_links.append(href)
            # Match high-res images / originals
            elif any(ext in href.lower() for ext in [".jpg", ".jpeg", ".png", ".webp", "originals"]) and href not in image_links:
                image_links.append(href)

        # 3. Fallback regex search for embedded URLs in script/html
        if not video_links:
            raw_vids = re.findall(r'<source[^>]+src=["\'](https?://[^"\']+)["\']', html)
            for v in raw_vids:
                cleaned = clean_media_url(v)
                if cleaned and cleaned not in video_links:
                    video_links.append(cleaned)

        if not image_links:
            raw_imgs = re.findall(r'(?:poster|src)=["\'](https?://[^"\']+\.(?:jpg|jpeg|png|webp))["\']', html)
            for img in raw_imgs:
                cleaned = clean_media_url(img)
                if cleaned and cleaned not in image_links:
                    image_links.append(cleaned)

        if not video_links and not image_links:
            return error_resp("No media found or download limit reached", 404)

        return success_resp({
            "target_url": url,
            "video": video_links[0] if video_links else None,
            "all_videos": video_links if len(video_links) > 1 else None,
            "photo": image_links[0] if image_links else None,
            "all_photos": image_links if len(image_links) > 1 else None
        })

    except requests.exceptions.Timeout:
        return error_resp("Upstream request timed out (>25s)", 504)
    except Exception as e:
        return error_resp(f"ExpertsTool request failed: {str(e)}", 500)

# ============================================================
# YOUTUBE ENGINE (Direct yt-dlp)
# ============================================================
def extract_youtube(url: str):
    if not url:
        return error_resp("YouTube URL is required")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []
        seen = set()
        for f in info.get("formats", []):
            d_url = f.get("url")
            if not d_url or d_url in seen or f.get("vcodec") == "none":
                continue
            seen.add(d_url)
            formats.append({
                "format_id": f.get("format_id"),
                "quality": f.get("format_note") or f.get("resolution") or "Original",
                "ext": f.get("ext", "mp4"),
                "has_audio": f.get("acodec") != "none",
                "url": d_url
            })

        return success_resp({
            "title": info.get("title", "Unknown"),
            "uploader": info.get("uploader", "Unknown"),
            "duration": info.get("duration"),
            "thumbnail": info.get("thumbnail"),
            "formats": formats
        })
    except Exception as e:
        return error_resp(f"YouTube extraction failed: {str(e)}", 500)

# ============================================================
# ROUTES
# ============================================================
@app.route('/', methods=['GET'])
def index():
    return jsonify({
        "status": "online",
        "engine": "ExpertsTool & yt-dlp",
        "endpoints": {
            "Snapchat": "/snap?video=URL",
            "Instagram": "/instagram?video=URL",
            "ShareChat": "/sharechat?video=URL",
            "Twitter_X": "/twitter?video=URL",
            "TikTok": "/tiktok?video=URL",
            "Facebook": "/facebook?video=URL",
            "Pinterest": "/pinterest?video=URL",
            "YouTube": "/youtube?video=URL"
        },
        **CREDITS
    })

def get_url_param():
    return request.values.get("video", "").strip()

# 1. Snapchat
@app.route('/snap', methods=['GET', 'POST'])
def snap():
    return extract_via_expertstool(get_url_param(), "snapchat-video-downloader/")

# 2. Instagram Reels
@app.route('/instagram', methods=['GET', 'POST'])
@app.route('/Instagram', methods=['GET', 'POST'])
def insta():
    return extract_via_expertstool(get_url_param(), "instagram-reels-video-downloader/")

# 3. ShareChat
@app.route('/sharechat', methods=['GET', 'POST'])
def sharechat():
    return extract_via_expertstool(get_url_param(), "sharechat-video-downloader/")

# 4. Twitter / X
@app.route('/twitter', methods=['GET', 'POST'])
@app.route('/x', methods=['GET', 'POST'])
def twitter():
    return extract_via_expertstool(get_url_param(), "twitter-video-downloader/")

# 5. TikTok
@app.route('/tiktok', methods=['GET', 'POST'])
def tiktok():
    return extract_via_expertstool(get_url_param(), "tiktok-video-downloader/")

# 6. Facebook
@app.route('/facebook', methods=['GET', 'POST'])
def facebook():
    return extract_via_expertstool(get_url_param(), "download-facebook-video/")

# 7. Pinterest
@app.route('/pinterest', methods=['GET', 'POST'])
@app.route('/Pinterest', methods=['GET', 'POST'])
def pinterest():
    return extract_via_expertstool(
        get_url_param(),
        "pinterest-video-download/",
        action_url="https://www.expertstool.com/pinterest-video-download/"
    )

# 8. YouTube
@app.route('/youtube', methods=['GET', 'POST'])
def youtube():
    return extract_youtube(get_url_param())

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5310, threaded=True)
