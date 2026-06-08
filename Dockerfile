FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN python -m pip install --no-cache-dir --upgrade pip

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5080

CMD ["gunicorn", "-w", "1", "--threads", "4", "--timeout", "300", "--bind", "0.0.0.0:5080", "pipeline.web.app:app"]
