FROM python:3.12-slim

WORKDIR /app

# Install backend dependencies
COPY src/backend/requirements.txt ./requirements-backend.txt
RUN pip install --no-cache-dir -r requirements-backend.txt

# Install ppt_generation dependencies (python-pptx, pandas, numpy, etc.)
COPY requirements.txt ./requirements-ppt.txt
RUN pip install --no-cache-dir -r requirements-ppt.txt

# Copy backend application code
COPY src/backend/app/ ./app/

# Copy ppt_generation module into src/ppt_generation/
# (config.py uses parents[3] from src/ppt_generation/core/config.py to find PROJECT_ROOT)
# Layout: /app/src/ppt_generation/core/config.py → parents[3] = /app = PROJECT_ROOT
COPY src/ppt_generation/ ./src/ppt_generation/

# Copy template.pptx where config.py expects it (PROJECT_ROOT/source/)
COPY source/template.pptx ./source/template.pptx

# Create outputs directory
RUN mkdir -p ./outputs

# Expose port
EXPOSE 8000

# Run with uvicorn
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
