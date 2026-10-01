# MediaRush cloud: accounts, login (JWT), users, folders & permissions, transfer METADATA dashboard.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DJANGO_DEBUG=0
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN DJANGO_SECRET_KEY=build P2P_CLOUD_JWT_SECRET=build-only-not-used-0123456789abcdef \
    python manage.py collectstatic --noinput -v 0

RUN useradd --system --uid 10002 cloud
USER cloud
EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${GUNICORN_WORKERS:-3} --access-logfile -"]
