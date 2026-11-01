# syntax=docker/dockerfile:1.7
FROM python:3.12-slim
LABEL org.opencontainers.image.title="sso-bridge" \
      org.opencontainers.image.description="OIDC (oauth2-proxy) to SwitchFrame operator-token bridge"
COPY bridge.py /app/bridge.py
USER 65532:65532
ENTRYPOINT ["python3", "-I", "/app/bridge.py"]
