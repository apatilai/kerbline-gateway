FROM python:3.12-slim

# Install Node.js 22 LTS for air-gapped V8 Code Mode sandbox
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY sandbox/ ./sandbox/

# Run as non-root user for Cloud Run Gen2 / gVisor hardening
RUN useradd -m -u 10001 kerbline && chown -R kerbline:kerbline /app
USER kerbline

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
