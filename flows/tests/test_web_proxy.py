"""The web container's nginx config and start-up validation, run for real.

Skipped when nginx/envsubst/openssl are not installed (`apt install nginx-light gettext-base`).
The template is rendered the way the image's entrypoint does (envsubst on API_TOKEN and
AUTH_BASIC), pointed at a stub API that echoes the headers it receives, and driven over HTTP.
The Alpine image itself (and its entrypoint wiring) is not built here.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "deploy/nginx.conf.template"
VALIDATOR = ROOT / "deploy/web-entrypoint.d/10-validate-env.sh"
TOKEN = "a1b2c3d4e5f6-test.token_~"
USER, PASSWORD = "me", "s3cret-pass"

NGINX = shutil.which("nginx") or "/usr/sbin/nginx"
needs_nginx = pytest.mark.skipif(
    not (Path(NGINX).exists() and shutil.which("envsubst") and shutil.which("openssl")),
    reason="nginx, envsubst and openssl are required",
)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Stub(BaseHTTPRequestHandler):
    """Stands in for the API: echoes what nginx forwarded."""

    def _reply(self):
        body = json.dumps(
            {
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_PUT = do_DELETE = _reply

    def log_message(self, *a):  # quiet
        pass


@pytest.fixture(scope="module")
def stub():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def render(tmp: Path, *, api_port: int, listen: int, auth: str, htpasswd: bool) -> Path:
    env = {**os.environ, "API_TOKEN": TOKEN, "AUTH_BASIC": auth}
    text = subprocess.run(
        ["envsubst", "${API_TOKEN} ${AUTH_BASIC}"],
        input=TEMPLATE.read_text(),
        capture_output=True,
        text=True,
        env=env,
        check=True,
    ).stdout
    www = tmp / "www"
    www.mkdir(exist_ok=True)
    (www / "index.html").write_text("<html>spa</html>")
    pw = tmp / "htpasswd"
    if htpasswd:
        hashed = subprocess.run(
            ["openssl", "passwd", "-apr1", PASSWORD], capture_output=True, text=True, check=True
        ).stdout.strip()
        pw.write_text(f"{USER}:{hashed}\n")
    text = (
        text.replace("http://api:8000", f"http://127.0.0.1:{api_port}")
        .replace("listen 8080;", f"listen 127.0.0.1:{listen};")
        .replace("/usr/share/nginx/html", str(www))
        .replace("/etc/nginx/htpasswd", str(pw))
    )
    site = tmp / "site.conf"
    site.write_text(text)
    conf = tmp / "nginx.conf"
    # as root nginx would drop its workers to "nobody", which cannot read pytest's private tmp dir
    user = "user root;" if os.geteuid() == 0 else ""
    conf.write_text(
        f"""
{user}
pid {tmp}/nginx.pid;
error_log {tmp}/error.log;
events {{}}
http {{
  include /etc/nginx/mime.types;  # the real image's nginx.conf does the same
  access_log off;
  client_body_temp_path {tmp}/cb; proxy_temp_path {tmp}/px; fastcgi_temp_path {tmp}/fc;
  uwsgi_temp_path {tmp}/uw; scgi_temp_path {tmp}/sc;
  include {site};
}}
"""
    )
    return conf


class Server:
    def __init__(self, tmp: Path, api_port: int, auth: str, htpasswd: bool = True):
        self.tmp, self.port = tmp, free_port()
        self.conf = render(tmp, api_port=api_port, listen=self.port, auth=auth, htpasswd=htpasswd)

    def test_config(self) -> subprocess.CompletedProcess:
        return subprocess.run(
            [NGINX, "-t", "-c", str(self.conf), "-p", str(self.tmp)], capture_output=True, text=True
        )

    def __enter__(self):
        self.proc = subprocess.Popen(
            [NGINX, "-c", str(self.conf), "-p", str(self.tmp), "-g", "daemon off;"],
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
                return self
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("nginx did not start")

    def __exit__(self, *exc):
        self.proc.terminate()
        self.proc.wait(timeout=10)

    def get(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read().decode()
        out = (resp.status, {k.lower(): v for k, v in resp.getheaders()}, body)
        conn.close()
        return out


def basic(user=USER, password=PASSWORD) -> dict:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()}


CSRF = {"X-Requested-With": "fin-app"}


@pytest.fixture
def secured(tmp_path, stub):
    with Server(tmp_path, stub, auth="fin-app") as s:
        yield s


@needs_nginx
def test_the_rendered_config_is_valid_with_and_without_auth(tmp_path, stub):
    for auth in ("fin-app", "off"):
        (tmp_path / auth).mkdir()
        result = Server(tmp_path / auth, stub, auth).test_config()
        assert result.returncode == 0, result.stderr


@needs_nginx
def test_basic_auth_protects_the_app_and_the_api_but_not_the_health_check(secured):
    assert secured.get("GET", "/")[0] == 401
    assert secured.get("GET", "/api/opportunities")[0] == 401
    assert secured.get("GET", "/", basic(password="wrong"))[0] == 401
    status, _, body = secured.get("GET", "/", basic())
    assert status == 200 and "spa" in body
    assert secured.get("GET", "/sandbox/1", basic())[2] == "<html>spa</html>"  # SPA fallback
    assert secured.get("GET", "/healthz")[0] == 200  # no credentials needed


@needs_nginx
def test_the_api_token_is_added_and_replaces_whatever_the_client_sent(secured):
    _, _, body = secured.get("GET", "/api/x", basic())
    assert json.loads(body)["authorization"] == f"Bearer {TOKEN}"  # not the basic credentials
    evil = {**basic(), "X-Requested-With": "fin-app"}
    _, _, body = secured.get("PUT", "/api/watchlist/AAA", evil)
    assert json.loads(body) == {
        "method": "PUT", "path": "/api/watchlist/AAA", "authorization": f"Bearer {TOKEN}",
    }  # fmt: skip


@needs_nginx
def test_state_changing_requests_need_the_csrf_header(secured):
    for method in ("POST", "PUT", "DELETE"):
        assert secured.get(method, "/api/alerts/1/read", basic())[0] == 403, method
        wrong = {**basic(), "X-Requested-With": "XMLHttpRequest"}
        assert secured.get(method, "/api/alerts/1/read", wrong)[0] == 403, method
        assert secured.get(method, "/api/alerts/1/read", {**basic(), **CSRF})[0] == 200, method
    assert secured.get("GET", "/api/alerts", basic())[0] == 200  # reads need nothing extra
    # and the header is no substitute for authentication
    assert secured.get("POST", "/api/alerts/1/read", CSRF)[0] == 401


@needs_nginx
def test_security_headers_are_set_even_on_errors(secured):
    for _, headers, _ in (secured.get("GET", "/"), secured.get("GET", "/", basic())):
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert "max-age=" in headers["strict-transport-security"]
        csp = headers["content-security-policy"]
        assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp


@needs_nginx
def test_a_flood_of_requests_is_rate_limited_but_the_health_check_is_not(secured):
    statuses = [secured.get("GET", "/", basic(password="guess"))[0] for _ in range(150)]
    assert 503 in statuses and statuses[0] == 401  # the early attempts get through, then 503
    assert secured.get("GET", "/healthz")[0] == 200


@needs_nginx
def test_auth_off_serves_without_credentials(tmp_path, stub):
    with Server(tmp_path, stub, auth="off", htpasswd=False) as s:
        assert s.get("GET", "/")[0] == 200
        assert s.get("GET", "/api/x")[0] == 200
        assert json.loads(s.get("GET", "/api/x")[2])["authorization"] == f"Bearer {TOKEN}"


@needs_nginx
def test_a_missing_htpasswd_file_fails_closed(tmp_path, stub):
    with Server(tmp_path, stub, auth="fin-app", htpasswd=False) as s:
        # nginx refuses (403 on 1.24, 500 on some versions): never a 200, and never forwarded
        assert s.get("GET", "/", basic())[0] in (403, 500)
        assert s.get("GET", "/api/x", {**basic(), **CSRF})[0] in (403, 500)


# --- the start-up validator (a POSIX shell script, no nginx needed) ---------------------------


def run_validator(tmp_path: Path, **env) -> subprocess.CompletedProcess:
    htpasswd = tmp_path / "htpasswd"
    htpasswd.write_text("me:hash\n")
    base = {"PATH": os.environ["PATH"], "HTPASSWD_PATH": str(htpasswd)}
    return subprocess.run(
        ["sh", str(VALIDATOR)], env={**base, **env}, capture_output=True, text=True
    )


@pytest.mark.parametrize("token", ["abc123", "a.b_c~d-e", "0" * 64])
def test_validator_accepts_url_safe_tokens(tmp_path, token):
    assert run_validator(tmp_path, API_TOKEN=token, AUTH_BASIC="fin-app").returncode == 0


@pytest.mark.parametrize("token", ["", 'a"b', "a;b", "a$b", "a b", "a\nb", "a'b", "a{b}", "a\\b"])
def test_validator_rejects_tokens_that_could_break_the_config(tmp_path, token):
    r = run_validator(tmp_path, API_TOKEN=token, AUTH_BASIC="fin-app")
    assert r.returncode == 1 and "API_TOKEN" in r.stderr


def test_validator_checks_the_realm_and_the_htpasswd_file(tmp_path):
    ok = {"API_TOKEN": "abc"}
    assert run_validator(tmp_path, **ok, AUTH_BASIC="off").returncode == 0
    assert run_validator(tmp_path, **ok, AUTH_BASIC="my site").returncode == 0
    assert run_validator(tmp_path, **ok).returncode == 1  # AUTH_BASIC unset
    assert run_validator(tmp_path, **ok, AUTH_BASIC='bad"realm').returncode == 1
    assert run_validator(tmp_path, **ok, AUTH_BASIC="x;y").returncode == 1
    missing = run_validator(
        tmp_path, **ok, AUTH_BASIC="fin-app", HTPASSWD_PATH=str(tmp_path / "no")
    )
    assert missing.returncode == 1 and "htpasswd" in missing.stderr
    (tmp_path / "empty").write_text("")
    empty = run_validator(
        tmp_path, **ok, AUTH_BASIC="fin-app", HTPASSWD_PATH=str(tmp_path / "empty")
    )
    assert empty.returncode == 1
    # with auth off no htpasswd file is needed
    assert (
        run_validator(tmp_path, **ok, AUTH_BASIC="off", HTPASSWD_PATH="/nonexistent").returncode
        == 0
    )
