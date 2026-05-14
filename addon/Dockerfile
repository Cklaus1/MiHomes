ARG BUILD_FROM=ghcr.io/home-assistant/base-python:3.12-alpine3.21
FROM ${BUILD_FROM}

# Build deps for Python packages with C extensions (SQLAlchemy, cryptography, etc.)
RUN apk add --no-cache \
    gcc \
    musl-dev \
    libffi-dev \
    openssl-dev \
    sqlite \
    bash

WORKDIR /app

# Copy the project
COPY pyproject.toml /app/
COPY src/ /app/src/

# Install MiHomes and all Python dependencies
RUN pip install --no-cache-dir -e /app

# Copy the add-on run script
COPY addon/run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]

LABEL \
    io.hass.name="MiHomes Estate Manager" \
    io.hass.description="AI-first multi-home estate management" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version="1.0.0"
