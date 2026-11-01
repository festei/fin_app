#!/bin/sh
# Runs before the nginx image renders its templates. Refuses to start with a configuration that
# would break the rendered nginx config or silently run without auth.
set -eu

fail() { echo "fin-web: $1" >&2; exit 1; }

[ -n "${API_TOKEN:-}" ] || fail "API_TOKEN is not set"
case "$API_TOKEN" in
  *[!A-Za-z0-9._~-]*) fail "API_TOKEN may only contain letters, digits and . _ ~ - (use: openssl rand -hex 32)" ;;
esac

[ -n "${AUTH_BASIC:-}" ] || fail "AUTH_BASIC is not set (use a realm name, or off for development)"
if [ "$AUTH_BASIC" != "off" ]; then
  case "$AUTH_BASIC" in
    *[!A-Za-z0-9\ ._-]*) fail "AUTH_BASIC (the realm) may only contain letters, digits, spaces and . _ -" ;;
  esac
  [ -s "${HTPASSWD_PATH:-/etc/nginx/htpasswd}" ] || fail "AUTH_BASIC is on but the htpasswd file is missing or empty (mount one at /etc/nginx/htpasswd)"
fi
