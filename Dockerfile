FROM python:3.11-slim

WORKDIR /app

# Python bağımlılıkları
RUN pip install flask

# Uygulama dosyalarını kopyala
COPY app.py .
COPY config.json .
COPY templates/ ./templates/

# Uygulama çalıştır
CMD ["python3", "app.py"]
