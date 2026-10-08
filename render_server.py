import os
import sys
import json
import re
import time
import urllib.request
import urllib.parse
from http.server import SimpleHTTPRequestHandler
import socketserver

# UTF-8 Encoding safe on all platforms
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PORT = int(os.environ.get("PORT", 10000))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

DOC_CACHE = {}

class RenderBackendHandler(SimpleHTTPRequestHandler):

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def send_json(self, data, code=200):
        try:
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. Health Route (Render Anti-Sleep Ping Target)
        if path == "/health" or path == "/api/health":
            self.send_json({
                "status": "healthy",
                "uptime": "active",
                "service": "Scribd Turbo PDF Backend",
                "timestamp": int(time.time()),
                "message": "Server is awake and ready!"
            }, 200)
            return

        # 2. Root Index Route
        if path == "/":
            self.send_json({
                "service": "Scribd Turbo PDF Downloader - Cloud Backend",
                "status": "online",
                "version": "2.0.0",
                "health_check": "/health",
                "author": "Engineered by Jayant Regar for Anuradha Regar"
            }, 200)
            return

        # 3. Scribd Document Manifest Resolver
        if path == "/api/manifest":
            doc_id = query.get("id", [""])[0] or query.get("url", [""])[0]
            if not doc_id:
                self.send_json({"success": False, "error": "Missing doc id or url"}, 400)
                return

            # Extract clean numeric ID
            m = re.search(r"(\d{6,12})", doc_id)
            clean_id = m.group(1) if m else doc_id

            # Check in-memory cache for instant 0.001s response
            if clean_id in DOC_CACHE:
                self.send_json(DOC_CACHE[clean_id], 200)
                return

            result = self.resolve_scribd_doc(clean_id)
            if result.get("success"):
                DOC_CACHE[clean_id] = result
            self.send_json(result, 200 if result.get("success") else 500)
            return

        # 4. High-Speed Image & Asset Proxy
        if path == "/api/proxy":
            target_url = query.get("url", [""])[0]
            if not target_url:
                self.send_json({"error": "Missing target url parameter"}, 400)
                return
            try:
                req = urllib.request.Request(target_url, headers=HEADERS)
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = resp.read()
                    content_type = resp.headers.get("Content-Type", "image/jpeg")
                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "public, max-age=86400")
                    self.end_headers()
                    self.wfile.write(data)
                return
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
                return

        # 404 for unknown endpoints
        self.send_json({"error": "Endpoint not found", "available": ["/health", "/api/manifest", "/api/proxy"]}, 404)

    def resolve_scribd_doc(self, doc_id):
        embed_url = f"https://www.scribd.com/embeds/{doc_id}/content?start_page=1&view_mode=scroll"
        doc_page_url = f"https://www.scribd.com/document/{doc_id}/"

        html = ""
        doc_html = ""
        try:
            req = urllib.request.Request(embed_url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=4) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception:
            pass

        # Fallback to doc_page_url only if embed failed
        if not html:
            try:
                req2 = urllib.request.Request(doc_page_url, headers=HEADERS)
                with urllib.request.urlopen(req2, timeout=4) as resp2:
                    doc_html = resp2.read().decode("utf-8", errors="ignore")
            except Exception:
                pass

        if not html and not doc_html:
            return {"success": False, "error": "Unable to connect to Scribd. Please check your document link or ID."}

        # Extract Title
        title = f"Document_{doc_id}"
        combined_text = doc_html + "\n" + html

        embed_title_m = re.search(r'EmbedsShow\([^\)]*?"title"\s*:\s*"([^"]+)"', combined_text)
        if embed_title_m:
            title = embed_title_m.group(1).split("|")[0].strip()
        else:
            meta_m = re.search(r'<meta\s+property=[\'"]og:title[\'"]\s+content=[\'"](.*?)[\'"]', combined_text, re.IGNORECASE)
            if meta_m:
                title = meta_m.group(1).split("|")[0].strip()
            else:
                title_m = re.search(r"<title>(.*?)</title>", combined_text, re.IGNORECASE)
                if title_m:
                    t = title_m.group(1).split("|")[0].strip()
                    if t and t.lower() != "scribd":
                        title = t

        title = re.sub(r'[\\/*?:"<>|]', "", title).strip() or f"Document_{doc_id}"

        # Universal Page & Image Parser
        unique_pages = {}
        search_html = html + "\n" + doc_html

        # Pattern 1: Images in orig/src/url (.png, .jpg, .webp)
        img_matches = re.findall(
            r'(?:orig|src|url)=[\'"](?:https?:)?//(?:html\d?\.scribd(?:assets)?\.com)/([a-z0-9]+)/images/(\d+)-([a-z0-9]+)\.(png|jpg|webp)[\'"]',
            search_html,
            re.IGNORECASE
        )
        for h, p, ph, ext in img_matches:
            p_num = int(p)
            if p_num not in unique_pages:
                unique_pages[p_num] = {
                    "page": p_num,
                    "hash": h,
                    "pageHash": ph,
                    "ext": ext,
                    "imgUrl": f"https://html.scribdassets.com/{h}/images/{p}-{ph}.{ext}"
                }

        # Pattern 2: JSONP matches
        jsonp_matches = re.findall(
            r"https?://html\d?\.scribdassets\.com/([a-z0-9]+)/pages/(\d+)-([a-z0-9]+)\.jsonp",
            search_html,
            re.IGNORECASE
        )
        for h, p, ph in jsonp_matches:
            p_num = int(p)
            if p_num not in unique_pages:
                unique_pages[p_num] = {
                    "page": p_num,
                    "hash": h,
                    "pageHash": ph,
                    "ext": "jpg",
                    "imgUrl": f"https://html.scribdassets.com/{h}/images/{p}-{ph}.jpg"
                }

        pages_list = [unique_pages[k] for k in sorted(unique_pages.keys())]

        # Pattern 3: docManager.assetPrefix fallback
        if not pages_list:
            prefix_match = re.search(r'docManager\.assetPrefix\s*=\s*[\'"]([a-z0-9]+)[\'"]', search_html)
            if prefix_match:
                hash_val = prefix_match.group(1)
                count_m = re.search(r'"page_count"\s*:\s*(\d+)', search_html)
                max_p = int(count_m.group(1)) if count_m else 10
                for p in range(1, max_p + 1):
                    pages_list.append({
                        "page": p,
                        "hash": hash_val,
                        "pageHash": "",
                        "ext": "jpg",
                        "imgUrl": f"https://html.scribdassets.com/{hash_val}/images/{p}.jpg"
                    })

        if not pages_list:
            return {"success": False, "error": "Could not find document pages. The document might be private or requires login."}

        return {
            "success": True,
            "docId": doc_id,
            "title": title,
            "totalPages": len(pages_list),
            "pages": pages_list
        }

# Top-level handler exports for Vercel/WSGI compatibility
handler = RenderBackendHandler
app = RenderBackendHandler

def run_server():
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("0.0.0.0", PORT), RenderBackendHandler) as httpd:
        print(f"==================================================")
        print(f"🚀 Render Turbo Backend Started on Port {PORT}")
        print(f"🔗 Health Route: http://0.0.0.0:{PORT}/health")
        print(f"⚡ Ready to serve mobile APK requests 24/7!")
        print(f"==================================================")
        sys.stdout.flush()
        httpd.serve_forever()

if __name__ == "__main__":
    run_server()
