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
