FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt
RUN pip install psycopg2-binary

COPY . .

RUN chmod +x prestart.sh

ENTRYPOINT ["/app/prestart.sh"]
CMD ["python", "main.py"]

