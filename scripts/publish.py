#!/usr/bin/env python3
"""Publish due, approved Instagram carousel posts (runs in GitHub Actions).

Layout: posts/<name>/01.jpg..NN.jpg, caption.txt, meta.json
meta.json: {"publish_at": ISO8601, "status": "approved|published|failed|...", "images": [...], "alt": [...]}
Images are served by GitHub Pages from this repo. The access token comes from the IG_TOKEN env var.
"""
import json, os, subprocess, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS = os.path.join(ROOT, "posts")
PAGES = "https://9959930-code.github.io/instagram"
API = "https://graph.instagram.com/v23.0"
TOKEN = os.environ.get("IG_TOKEN", "").strip()


def api(method, path, **params):
    params["access_token"] = TOKEN
    data = urllib.parse.urlencode(params)
    if method == "GET":
        req = urllib.request.Request(f"{API}/{path}?{data}")
    else:
        req = urllib.request.Request(f"{API}/{path}", data=data.encode(), method="POST")
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{path}: {e.read().decode()[:300]}") from None


def wait_served(url):
    for _ in range(60):
        try:
            if urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=15).status == 200:
                return
        except Exception:
            pass
        time.sleep(10)
    raise RuntimeError(f"image not served: {url}")


def wait_ready(cid):
    for _ in range(36):
        st = api("GET", cid, fields="status_code").get("status_code")
        if st == "FINISHED":
            return
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"container {cid} {st}")
        time.sleep(5)
    raise RuntimeError(f"container {cid} not ready")


def publish(name, meta):
    folder = os.path.join(POSTS, name)
    caption = open(os.path.join(folder, "caption.txt"), encoding="utf-8").read().strip()
    alts = meta.get("alt", [])
    ig = api("GET", "me", fields="user_id")["user_id"]
    children = []
    for i, img in enumerate(meta["images"]):
        url = f"{PAGES}/posts/{name}/{img}"
        wait_served(url)
        params = {"image_url": url, "is_carousel_item": "true"}
        if i < len(alts):
            params["alt_text"] = alts[i]
        cid = api("POST", f"{ig}/media", **params)["id"]
        wait_ready(cid)
        children.append(cid)
    parent = api("POST", f"{ig}/media", media_type="CAROUSEL", children=",".join(children), caption=caption)["id"]
    wait_ready(parent)
    media = api("POST", f"{ig}/media_publish", creation_id=parent)["id"]
    return media, api("GET", media, fields="permalink").get("permalink")


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args]).returncode


def claim(name, mp, meta):
    """Mark the post as publishing and push before posting; the Mac does the same, and only one push can win."""
    meta.update(status="publishing", claimed_by="github", claimed_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    json.dump(meta, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    git("add", mp)
    git("-c", "user.name=github-actions[bot]", "-c", "user.email=41898283+github-actions[bot]@users.noreply.github.com",
        "commit", "-q", "-m", f"Claim {name} for publishing (github)")
    if git("push", "-q", "origin", "HEAD:main") != 0:
        git("reset", "-q", "--hard", "HEAD~1")
        print(f"SKIP {name}: the Mac claimed it first")
        return False
    return True


def main():
    if not TOKEN:
        sys.exit("IG_TOKEN is not set")
    now = datetime.now(timezone.utc)
    changed = False
    for name in sorted(os.listdir(POSTS)):
        mp = os.path.join(POSTS, name, "meta.json")
        if not os.path.exists(mp):
            continue
        meta = json.load(open(mp, encoding="utf-8"))
        if meta.get("status") != "approved" or datetime.fromisoformat(meta["publish_at"]) > now:
            continue
        if not claim(name, mp, meta):
            continue
        try:
            media, link = publish(name, meta)
            meta.update(status="published", media_id=media, permalink=link,
                        published_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
            print(f"PUBLISHED {name} -> {link}")
        except Exception as e:
            meta.update(status="failed", error=str(e)[:500])
            print(f"FAILED {name}: {e}")
        json.dump(meta, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        changed = True
    if not changed:
        print("nothing due")


if __name__ == "__main__":
    main()
