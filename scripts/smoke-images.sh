#!/usr/bin/env bash
# Local smoke test for the container images (same checks the disabled CI image job runs).
# Usage: scripts/smoke-images.sh        (needs a running Docker daemon)
set -euo pipefail
cd "$(dirname "$0")/.."

build() { docker build -q -f "$1" ${3:+--build-arg "$3"} -t "$2:smoke" . >/dev/null && echo "built $2"; }

build deploy/python.Dockerfile fin-api PACKAGE=fin-api
build deploy/python.Dockerfile fin-flows PACKAGE=fin-flows
build deploy/web.Dockerfile fin-web

# Python images must import the installed (non-editable) packages and find config/.
docker run --rm fin-api:smoke python -c \
  "import fin_api.main, fin_core.graph as g; g.build_graph(); g.load_config(); print('fin-api import ok')"
docker run --rm fin-flows:smoke python -c "import fin_flows.scoring; print('fin-flows import ok')"
docker run --rm --entrypoint test fin-web:smoke -f /usr/share/nginx/html/index.html && echo "fin-web build present"

# The web image refuses an unsafe configuration and renders a valid nginx config otherwise.
if docker run --rm -e API_TOKEN='a"b' -e AUTH_BASIC=off fin-web:smoke true >/dev/null 2>&1; then
  echo "FAIL: fin-web started with an API_TOKEN that could break the nginx config" >&2
  exit 1
fi
if docker run --rm -e API_TOKEN=abc123 -e AUTH_BASIC=fin-app fin-web:smoke true >/dev/null 2>&1; then
  echo "FAIL: fin-web started with basic auth on and no htpasswd file" >&2
  exit 1
fi
docker run --rm --add-host api:127.0.0.1 -e API_TOKEN=abc123 -e AUTH_BASIC=off \
  fin-web:smoke nginx -t 2>&1 | grep -q "test is successful" && echo "fin-web renders a valid nginx config"

# All images must run as a non-root user.
for img in fin-api fin-flows fin-web; do
  user=$(docker image inspect "$img:smoke" --format '{{.Config.User}}')
  if [ -z "$user" ] || [ "$user" = "root" ] || [ "$user" = "0" ]; then
    echo "FAIL: $img runs as root (user='$user')" >&2
    exit 1
  fi
  echo "$img runs as '$user'"
done
echo "all image smoke tests passed"
