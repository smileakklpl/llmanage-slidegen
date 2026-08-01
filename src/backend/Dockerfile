FROM python:3.12-slim

WORKDIR /app

# Native libraries required by OpenCV / PaddleOCR / PaddlePaddle.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install application dependencies and OCR dependencies.
COPY requirements.txt /tmp/requirements-root.txt
COPY src/backend/requirements.txt /tmp/requirements-backend.txt
COPY src/backend/requirements-ocr.txt /tmp/requirements-ocr.txt

RUN pip install --no-cache-dir \
    -r /tmp/requirements-root.txt \
    -r /tmp/requirements-backend.txt \
    -r /tmp/requirements-ocr.txt

# PaddleOCR does not bundle the PaddlePaddle inference engine.
RUN python -m pip install --no-cache-dir \
    paddlepaddle==3.2.0 \
    -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
COPY src/core/ ./src/core/
COPY src/ppt_generation/ ./src/ppt_generation/
COPY src/backend/app/ ./src/backend/app/
COPY prompts/ ./prompts/
COPY config/ ./config/
COPY source/template.pptx ./source/template.pptx
COPY main.py ./main.py

ENV PYTHONPATH=/app/src:/app/src/backend

EXPOSE 8000

CMD ["python", "main.py"]
