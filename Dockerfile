# Eyes Of Aziz bridge for a Raspberry Pi, NAS or any Docker host.
# Built for linux/amd64 and linux/arm64 by .github/workflows/docker.yml.
#
#   Set up cameras (needs the host network to find them):
#     docker run -it --rm --network host -v "$PWD:/data" \
#       --entrypoint eyes-of-aziz-setup ghcr.io/mastashake08/eyes-of-aziz
#   Run every camera in ./cameras:
#     docker run -d --restart unless-stopped -v "$PWD:/data" -p 5152:5152 \
#       ghcr.io/mastashake08/eyes-of-aziz
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY eyes_of_aziz ./eyes_of_aziz
RUN pip install . && eyes-of-aziz-bridge --self-test

# Runs as an ordinary user; camera configs and the registration tokens in
# them live in the /data volume.
RUN useradd --create-home --uid 1000 aziz
USER aziz
WORKDIR /data
VOLUME /data

EXPOSE 5152
HEALTHCHECK --interval=60s --timeout=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5152/status.json', timeout=4)" || exit 1

ENTRYPOINT ["eyes-of-aziz-bridge"]
CMD ["--all", "--cameras-dir", "/data/cameras", "--status-host", "0.0.0.0"]
