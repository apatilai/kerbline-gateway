FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pii_vault.py pre_pass.py doc_normaliser.py app.py ./

RUN useradd -m -u 10001 kerbline
USER kerbline

EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
