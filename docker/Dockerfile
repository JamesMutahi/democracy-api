# Use the official Python runtime image.
FROM python:3.12.3

# Python runtime behavior.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Install geospatial and PostgreSQL client library dependencies.
RUN apt-get update && apt-get install -y --no-install-recommends \
    binutils \
    libproj-dev \
    gdal-bin \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root application user.
RUN useradd --create-home --shell /usr/sbin/nologin appuser

# Create and set the application working directory.
WORKDIR /democracy

# Upgrade pip.
RUN python -m pip install --upgrade pip

# Copy requirements first for better Docker layer caching.
COPY requirements.txt requirements.txt

# Install Python dependencies.
RUN python -m pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application.
COPY . .

# Explicitly copy the entrypoint so the build fails early if it is missing.
COPY docker/entrypoint.sh /democracy/docker/entrypoint.sh

# Remove Windows line endings and make executable.
RUN sed -i 's/\r$//' /democracy/docker/entrypoint.sh \
    && chmod +x /democracy/docker/entrypoint.sh

# Create a writable directory for Celery Beat schedule persistence.
RUN mkdir -p /data && chown -R appuser:appuser /democracy /data

# Run the application as a non-root user.
USER appuser

# Expose the Django/Uvicorn port.
EXPOSE 8000

# Use an absolute entrypoint path.
# Use /bin/sh explicitly to avoid shebang-related exec issues.
ENTRYPOINT ["/bin/sh", "/democracy/docker/entrypoint.sh"]

# Default command if no Compose command is provided.
CMD ["uvicorn", "project.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--workers", "3", "--proxy-headers", "--forwarded-allow-ips=*"]