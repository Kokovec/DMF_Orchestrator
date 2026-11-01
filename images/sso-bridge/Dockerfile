# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159
LABEL org.opencontainers.image.title="sso-bridge" \
      org.opencontainers.image.description="OIDC (oauth2-proxy) to SwitchFrame operator-token bridge"
COPY bridge.py /app/bridge.py
USER 65532:65532
ENTRYPOINT ["python3", "-I", "/app/bridge.py"]
