FROM python:3.11-slim

# Build dependencies for evdev (C extension) + required linux headers
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    linux-libc-dev \
    udev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir pyserial evdev

COPY arm_gamepad.py /app/arm_gamepad.py

CMD ["python3", "/app/arm_gamepad.py"]
