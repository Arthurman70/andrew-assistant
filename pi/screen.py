"""Screen UI on Pi loopback; pairing credentials never enter the browser."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import ssl
import urllib.request

BASE = Path(__file__).resolve().parent
CONFIG_BASE=Path(os.environ.get('ANDREW_BASE_DIR',str(BASE)))
CONFIG = json.loads((CONFIG_BASE / 'relay-config.json').read_text())
TLS = ssl.create_default_context(cafile=str(CONFIG_BASE / 'pc.crt'))


class Screen(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, code, data, kind='application/json'):
        if not isinstance(data, bytes):
            data = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Frame-Options', 'DENY')
        self.end_headers()
        self.wfile.write(data)

    def proxy(self, path, payload=None):
        try:
            req = urllib.request.Request(CONFIG['pc_url'] + path,
                None if payload is None else json.dumps(payload).encode(),
                {'Authorization': 'Bearer ' + CONFIG['token'], 'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, context=TLS, timeout=180) as response:
                self.reply(200, response.read(),response.headers.get('Content-Type','application/json'))
        except Exception:
            self.reply(503, {'error': 'PC unavailable. Check that Andrew is running and the PC is awake.'})

    def valid(self):
        return self.headers.get('Host') in ('127.0.0.1:8770', 'localhost:8770') and self.headers.get('Origin', 'http://127.0.0.1:8770') in ('http://127.0.0.1:8770', 'http://localhost:8770')

    def do_GET(self):
        if not self.valid():
            return self.reply(403, {'error': 'Local display only.'})
        if self.path == '/':
            return self.reply(200, (BASE / 'screen.html').read_bytes(), 'text/html; charset=utf-8')
        if self.path == '/api/status':
            return self.proxy(self.path)
        if self.path in ('/assets/upgrade.js','/assets/upgrade.css','/assets/weather.js'):
            return self.proxy(self.path)
        if self.path.split('?')[0]=='/api/camera-frame':
            return self.proxy(self.path)
        if self.path == '/assets/andrew.png':
            return self.reply(200,(BASE/'andrew.png').read_bytes(),'image/png')
        if self.path == '/assets/display-test.mp4' and (BASE/'display-test.mp4').is_file():
            return self.reply(200,(BASE/'display-test.mp4').read_bytes(),'video/mp4')
        self.reply(404, {'error': 'Not found.'})

    def do_POST(self):
        if not self.valid() or self.headers.get('X-Andrew-Screen') != '1':
            return self.reply(403, {'error': 'Local display only.'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 1 <= length <= 8000 or self.path not in ('/api/command','/api/speech-control'):
                raise ValueError()
            data = json.loads(self.rfile.read(length))
            if self.path=='/api/speech-control':return self.proxy(self.path,{'action':data.get('action'),'source':'pi'})
            return self.proxy('/api/command', {'text': data['text'], 'speak': True})
        except (ValueError, KeyError):
            self.reply(400, {'error': 'Invalid command.'})


ThreadingHTTPServer(('127.0.0.1', 8770), Screen).serve_forever()
