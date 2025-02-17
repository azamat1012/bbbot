FROM python:3.10-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    libpng-dev \
    libfreetype6-dev \
    poppler-utils

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# CMD ["gunicorn", "-b", "0.0.0.0:$PORT", "main:app"]

CMD gunicorn -b 0.0.0.0:$PORT main:app
