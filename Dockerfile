FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN sed -i 's/\r$//' prestart.sh && chmod +x prestart.sh

ENTRYPOINT ["/app/prestart.sh"]
CMD ["python", "main.py"]
