# filepath: c:\job\proxy_purchase_systems\Dockerfile
# Use Python runtime image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Install system dependencies required for some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create upload directory
RUN mkdir -p uploaded_pdfs

# Expose the port Cloud Run expects
EXPOSE 8080

# Run the application with uvicorn
# Cloud Run expects the PORT environment variable
CMD ["uvicorn", "purchase_server:app", "--host", "0.0.0.0", "--port", "8080"]