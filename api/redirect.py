import requests
from http.server import BaseHTTPRequestHandler

GIST_RAW_URL = "https://gist.githubusercontent.com/Rensushii/02945cbdc4abe5148470106e8a8648b8/raw/tunnel_url.txt"

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            resp = requests.get(GIST_RAW_URL, timeout=5)
            tunnel_url = resp.text.strip()
            if tunnel_url.startswith("https://"):
                self.send_response(302)
                self.send_header("Location", tunnel_url)
                self.end_headers()
                return
        except Exception:
            pass
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h1>Greenhouse dashboard is offline. Please try again shortly.</h1>")
