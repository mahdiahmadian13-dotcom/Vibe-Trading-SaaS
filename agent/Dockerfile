FROM python:3.11-slim

WORKDIR /agent

# httpx + docker CLI (compose v2 plugin) — the agent drives the HOST daemon
# via the mounted socket, so it needs its own CLI binaries.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc && \
    chmod a+r /etc/apt/keyrings/docker.asc && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
      > /etc/apt/sources.list.d/docker.list && \
    apt-get update && apt-get install -y --no-install-recommends \
      docker-cli docker-compose-plugin && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir httpx

COPY agent/app/main.py ./main.py

# runs from /agent-project (the mounted project dir) via compose `command`
CMD ["python", "/agent/main.py"]
