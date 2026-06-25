FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv ca-certificates curl apt-transport-https libicu70 \
    && ln -s /usr/bin/python3 /usr/local/bin/python \
    && ARCH=$(uname -m) \
    && if [ "$ARCH" = "aarch64" ]; then PWSH_ARCH="arm64"; else PWSH_ARCH="x64"; fi \
    && curl -fsSL "https://github.com/PowerShell/PowerShell/releases/download/v7.4.7/powershell-7.4.7-linux-${PWSH_ARCH}.tar.gz" -o /tmp/powershell.tar.gz \
    && mkdir -p /opt/microsoft/powershell/7 \
    && tar -xzf /tmp/powershell.tar.gz -C /opt/microsoft/powershell/7 \
    && chmod +x /opt/microsoft/powershell/7/pwsh \
    && ln -s /opt/microsoft/powershell/7/pwsh /usr/bin/pwsh \
    && rm /tmp/powershell.tar.gz \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN python -m venv /app/.venv \
    && /app/.venv/bin/python -m pip install --no-cache-dir --upgrade pip \
    && /app/.venv/bin/python -m pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD /app/.venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/catalog', timeout=3)"

ENTRYPOINT ["pwsh", "-NoLogo", "-NoProfile", "-File", "/app/docker-entrypoint.ps1"]

