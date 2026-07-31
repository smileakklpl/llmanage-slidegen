FROM python:3.12-slim

WORKDIR /app

# The backend now invokes core and ppt_generation in-process, so the image is
# built from the repository root and installs both pinned requirement sets.
COPY requirements.txt /tmp/requirements-root.txt
COPY src/backend/requirements.txt /tmp/requirements-backend.txt
RUN pip install --no-cache-dir \
    -r /tmp/requirements-root.txt \
    -r /tmp/requirements-backend.txt

COPY src/core/ ./src/core/
COPY src/ppt_generation/ ./src/ppt_generation/
COPY src/backend/app/ ./src/backend/app/
COPY prompts/ ./prompts/
COPY config/ ./config/
COPY source/template.pptx ./source/template.pptx

ENV PYTHONPATH=/app/src:/app/src/backend

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--app-dir", "src/backend", "--host", "0.0.0.0", "--port", "8000"]
