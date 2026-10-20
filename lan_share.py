import html
import mimetypes
import os
import posixpath
import secrets
import socket
import threading
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nova Files</title>
  <style>
    :root { color-scheme: dark; }
    body { margin: 0; font-family: Segoe UI, system-ui, sans-serif; background: #0f1117; color: #e8eaed; }
    header { padding: 18px 16px 10px; }
    h1 { margin: 0; font-size: 22px; }
    p.muted { color: #8b93a7; margin: 6px 0 0; font-size: 13px; }
    main { padding: 0 16px 32px; }
    form.card, .card { background: #161922; border: 1px solid #262b38; border-radius: 16px; padding: 14px; margin: 12px 0; }
    input, button { font: inherit; }
    input[type=password], input[type=text] {
      width: 100%; box-sizing: border-box; padding: 12px; border-radius: 12px;
      border: 1px solid #2a3142; background: #10131b; color: #fff;
    }
    button, .btn {
      display: inline-block; background: #4f7cff; color: #fff; border: 0;
      border-radius: 12px; padding: 12px 16px; font-weight: 600; text-decoration: none;
    }
    .row { display: flex; justify-content: space-between; gap: 12px; align-items: center;
           padding: 12px 0; border-bottom: 1px solid #262b38; }
    .row:last-child { border-bottom: 0; }
    .name { color: #e8eaed; text-decoration: none; word-break: break-all; }
    .size { color: #8b93a7; font-size: 12px; white-space: nowrap; }
  </style>
</head>
<body>
  <header>
    <h1>Nova Files</h1>
    <p class="muted">__STATUS__</p>
  </header>
  <main>__BODY__</main>
</body>
</html>
"""


def lan_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


class ShareServer:
    def __init__(self, root: str, port: int = 8765) -> None:
        self.root = os.path.abspath(root)
        self.port = port
        self.pin = f"{secrets.randbelow(10000):04d}"
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args) -> None:  # noqa: A002
                return

            def _authorized(self) -> bool:
                cookie = self.headers.get("Cookie", "")
                return f"nova_pin={owner.pin}" in cookie

            def _send_html(self, body: str, status: str, code: int = 200) -> None:
                page = PAGE.replace("__STATUS__", html.escape(status)).replace("__BODY__", body)
                data = page.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _login_page(self, error: str = "") -> None:
                msg = f"<p class='muted'>{html.escape(error)}</p>" if error else ""
                body = f"""
                <form class="card" method="post" action="/login">
                  {msg}
                  <p>PC ke PIN se connect karo</p>
                  <p><input type="password" name="pin" inputmode="numeric" placeholder="4-digit PIN"></p>
                  <p><button type="submit">Connect</button></p>
                </form>
                """
                self._send_html(body, "Same Wi-Fi file transfer")

            def _safe_path(self, rel: str) -> str | None:
                rel = unquote(rel).lstrip("/")
                full = os.path.abspath(os.path.join(owner.root, rel))
                try:
                    if os.path.commonpath([owner.root, full]) != owner.root:
                        return None
                except ValueError:
                    return None
                return full

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path == "/login":
                    self._login_page()
                    return
                if not self._authorized():
                    self._login_page()
                    return
                if parsed.path.startswith("/download/"):
                    target = self._safe_path(parsed.path[len("/download/") :])
                    if not target or not os.path.isfile(target):
                        self._send_html("<p>File nahi mili.</p>", "Error", 404)
                        return
                    self._send_file(target)
                    return
                rel = parsed.path
                target = self._safe_path(rel)
                if not target or not os.path.isdir(target):
                    self._send_html("<p>Folder nahi mila.</p>", "Error", 404)
                    return
                self._send_html(self._listing(target, rel), os.path.basename(owner.root) or owner.root)

            def do_POST(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length", "0"))
                if parsed.path == "/login":
                    raw = self.rfile.read(length)
                    pin = parse_qs(raw.decode("utf-8", "ignore")).get("pin", [""])[0].strip()
                    if pin != owner.pin:
                        self._login_page("Galat PIN")
                        return
                    self.send_response(302)
                    self.send_header("Set-Cookie", f"nova_pin={owner.pin}; Path=/; HttpOnly")
                    self.send_header("Location", "/")
                    self.end_headers()
                    return
                if not self._authorized():
                    self._login_page()
                    return
                if parsed.path != "/upload":
                    self._send_html("<p>Invalid request.</p>", "Error", 400)
                    return
                rel = parse_qs(urlparse(self.path).query).get("dir", [""])[0]
                folder = self._safe_path(rel)
                if not folder or not os.path.isdir(folder):
                    folder = owner.root
                filename, data = self._read_upload(length)
                if not filename or data is None:
                    self._send_html("<p>Upload fail. File choose karo.</p>", "Upload", 400)
                    return
                dest = os.path.join(folder, os.path.basename(filename))
                with open(dest, "wb") as handle:
                    handle.write(data)
                location = "/" if rel in ("", "/") else "/" + rel.strip("/")
                self.send_response(302)
                self.send_header("Location", location)
                self.end_headers()

            def _read_upload(self, length: int) -> tuple[str | None, bytes | None]:
                if length <= 0 or length > 2 * 1024 * 1024 * 1024:
                    return None, None
                payload = (
                    f"Content-Type: {self.headers.get('Content-Type', '')}\r\n\r\n".encode()
                    + self.rfile.read(length)
                )
                message = BytesParser(policy=policy.default).parsebytes(payload)
                for part in message.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if name != "file":
                        continue
                    filename = part.get_filename()
                    if not filename:
                        return None, None
                    payload = part.get_payload(decode=True)
                    if payload is None:
                        return None, None
                    return Path(filename).name, payload
                return None, None

            def _listing(self, folder: str, rel: str) -> str:
                rel = posixpath.normpath(rel)
                parent = "" if rel in (".", "/", "") else posixpath.dirname(rel)
                rows = []
                if rel not in (".", "/", ""):
                    href = "/" if parent in (".", "/", "") else quote(parent)
                    rows.append(
                        f"<div class='row'><a class='name' href='{href}'>← Back</a><span class='size'></span></div>"
                    )
                try:
                    names = sorted(os.listdir(folder), key=str.lower)
                except OSError as exc:
                    return f"<p>{html.escape(str(exc))}</p>"
                for name in names:
                    path = os.path.join(folder, name)
                    child = posixpath.join(rel, name).replace("\\", "/").lstrip("/")
                    if os.path.isdir(path):
                        rows.append(
                            f"<div class='row'><a class='name' href='/{quote(child)}'>📁 {html.escape(name)}</a><span class='size'>Folder</span></div>"
                        )
                    else:
                        size = _fmt_size(os.path.getsize(path))
                        rows.append(
                            f"<div class='row'><a class='name' href='/download/{quote(child)}'>📄 {html.escape(name)}</a><span class='size'>{size}</span></div>"
                        )
                upload_dir = quote(rel.lstrip("/"))
                files = "".join(rows) or "<p class='muted'>Folder empty hai.</p>"
                return f"""
                <form class="card" method="post" action="/upload?dir={upload_dir}" enctype="multipart/form-data">
                  <p>Phone se file bhejo</p>
                  <p><input type="file" name="file"></p>
                  <p><button type="submit">Upload to PC</button></p>
                </form>
                <div class="card">{files}</div>
                """

            def _send_file(self, path: str) -> None:
                mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
                size = os.path.getsize(path)
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(size))
                self.send_header(
                    "Content-Disposition",
                    f"attachment; filename*=UTF-8''{quote(os.path.basename(path))}",
                )
                self.end_headers()
                with open(path, "rb") as handle:
                    shutil_copy = True
                    while shutil_copy:
                        chunk = handle.read(1024 * 256)
                        if not chunk:
                            break
                        self.wfile.write(chunk)

        self._handler = Handler

    def start(self) -> None:
        self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), self._handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None

    @property
    def url(self) -> str:
        return f"http://{lan_ip()}:{self.port}"


def _fmt_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
