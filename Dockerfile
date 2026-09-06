FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV FXGLITCH_VPS=1 FXGLITCH_HOST=0.0.0.0 FXGLITCH_PORT=8765
EXPOSE 8765
CMD ["python", "serve.py", "--host", "0.0.0.0", "--port", "8765", "--no-open"]
