FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir requests schedule python-dateutil

COPY sync_mango.py .

CMD ["python", "sync_mango.py"]
