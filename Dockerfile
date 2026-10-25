FROM python:3.10-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Tesseract：PDF 中文 OCR；LibreOffice：.doc 老格式转换；Noto CJK：中文文档渲染
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-chi-tra \
    libreoffice-writer fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ ./agents/
COPY api/ ./api/
COPY config/ ./config/
COPY core/ ./core/
COPY tools/ ./tools/
COPY data/ ./data/
COPY .env.example .env.example

EXPOSE 8000

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]