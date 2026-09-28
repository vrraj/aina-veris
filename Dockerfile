# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# System libraries needed by docling's OpenCV/PyMuPDF layout pipeline
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libxcb1 \
    && rm -rf /var/lib/apt/lists/*

# Copy the requirements file and install dependencies first
# This improves layer caching performance. This is the line that executes
# when you build the image, installing all dependencies.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
# This assumes your structure is /backend, start.py, requirements.txt, etc.
COPY . /app

# Expose the port used by Uvicorn
EXPOSE 8000
