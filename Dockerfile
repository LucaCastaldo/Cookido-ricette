FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY converter.py server.py ./
ENV PORT=8000 COOKIDOO_COUNTRY=it COOKIDOO_LANGUAGE=it-IT PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["python", "server.py"]
