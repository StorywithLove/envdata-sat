FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY src/radiation_extract.py /app/src/radiation_extract.py
COPY config/cities.json /app/config/cities.json
COPY config/custom_locations.example.csv /app/config/custom_locations.example.csv

RUN pip install --no-cache-dir --disable-pip-version-check requests

ENTRYPOINT ["python", "/app/src/radiation_extract.py"]
CMD ["cities"]
