FROM python:3.12-alpine

# Evita archivos .pyc y fuerza logs en tiempo real
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    CHECK_INTERVAL_SECONDS=300

WORKDIR /app

COPY monitor.py /app/monitor.py
RUN mkdir -p /app/data

EXPOSE 8080

CMD ["python3", "monitor.py"]
