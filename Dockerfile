FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FAN_DATA_DIR=/app/data \
    FAN_PORT=5000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY pifan/ pifan/
COPY templates/ templates/
COPY static/ static/

VOLUME /app/data
EXPOSE 5000

# app.py handles SIGTERM itself and hands the fan back to the kernel at full speed.
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,sys,urllib.request; sys.exit(urllib.request.urlopen('http://127.0.0.1:%s/healthz' % os.environ['FAN_PORT'], timeout=4).status != 200)"

CMD ["python", "app.py"]
