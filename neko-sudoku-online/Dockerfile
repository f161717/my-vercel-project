FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8000

WORKDIR /app

COPY requirements.txt requirements-deploy.txt ./
RUN pip install --no-cache-dir -r requirements-deploy.txt

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 8 --timeout 60 app:app"]
