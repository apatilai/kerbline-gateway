FROM python:3.12-slim
WORKDIR /app
COPY index.html styles.css app.js traffic.js server.py telemetry.py telemetry_preview.py ./
RUN useradd -m -u 10001 kerbline && mkdir /data && chown kerbline:kerbline /data
USER kerbline
ENV PORT=8080 HOST=0.0.0.0 KERBLINE_DATA_PATH=/data/console.sqlite3
VOLUME ["/data"]
EXPOSE 8080
# CONSOLE_ACCESS_TOKEN and CONSOLE_ORIGIN must be supplied at runtime.
# TLS belongs at the customer-controlled reverse proxy.
CMD ["python3", "server.py"]
