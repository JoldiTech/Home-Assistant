ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:bookworm
FROM ${BUILD_FROM}

# CUPS with a driverless (IPP Everywhere) queue. Jobs arrive as finished PDFs;
# nothing is rendered here. iputils-ping for diagnostic.probe, tzdata so
# caller-ID records carry the shop's local time.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cups \
        cups-client \
        cups-filters \
        iputils-ping \
        python3 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY agent/ /opt/nmt-print-agent/

WORKDIR /opt/nmt-print-agent
CMD ["python3", "-u", "/opt/nmt-print-agent/agent.py"]
