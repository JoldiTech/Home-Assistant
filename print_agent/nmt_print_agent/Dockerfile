ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:bookworm
FROM ${BUILD_FROM}

# CUPS with a driverless (IPP Everywhere) queue; WeasyPrint renders HTML to PDF.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cups \
        cups-client \
        cups-filters \
        fonts-dejavu-core \
        fonts-liberation \
        python3 \
        weasyprint \
    && rm -rf /var/lib/apt/lists/*

COPY agent/ /opt/nmt-print-agent/

WORKDIR /opt/nmt-print-agent
CMD ["python3", "-u", "/opt/nmt-print-agent/agent.py"]
