FROM python:3.11-slim

# Install evdev build deps (usually not strictly needed with pip wheels, but safe)
RUN apt-get update && apt-get install -y --no-install-recommends \
    udev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python deps
RUN pip install --no-cache-dir pyserial evdev

# Copy app
COPY arm_gamepad.py /app/arm_gamepad.py

# Default command
CMD ["python3", "/app/arm_gamepad.py"]
