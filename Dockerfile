FROM python:3.12-slim
WORKDIR /app
COPY app.py index.html ./
ENV PORT=8080
CMD ["sh", "-c", "python app.py --host 0.0.0.0 --port ${PORT:-8080}"]
