#!/bin/sh
set -eu

fail() {
    printf '%s\n' "ERROR: unsafe container startup configuration" >&2
    exit 1
}

port="${PORT:-8000}"
case "$port" in
    ''|*[!0-9]*) fail ;;
esac
if [ "$port" -lt 1 ] || [ "$port" -gt 65535 ]; then
    fail
fi

runtime_environment="${ENVIRONMENT:-${ENV:-}}"
forwarded_allow_ips="${FORWARDED_ALLOW_IPS:-}"

case "$runtime_environment" in
    staging|preview|pilot|production)
        case "$forwarded_allow_ips" in
            '') fail ;;
            *[![:space:],]*) ;;
            *) fail ;;
        esac
        ;;
esac

if [ -n "$forwarded_allow_ips" ]; then
    case "$forwarded_allow_ips" in
        *'*'*|*'0.0.0.0/0'*|*'::/0'*) fail ;;
    esac
    exec uvicorn app.main:app \
        --host 0.0.0.0 \
        --port "$port" \
        --proxy-headers \
        --forwarded-allow-ips "$forwarded_allow_ips"
fi

exec uvicorn app.main:app --host 0.0.0.0 --port "$port"
