ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base-debian:bookworm
FROM ${BUILD_FROM}

# CUPS with a driverless (IPP Everywhere) queue. Jobs arrive as finished PDFs;
# nothing is rendered here. iputils-ping for diagnostic.probe, tzdata so
# caller-ID records carry the shop's local time. libatomic1 for Node 26.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        cups \
        cups-client \
        cups-filters \
        iputils-ping \
        libatomic1 \
        python3 \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# Node for CryptoCart's register box: the nodejs.org build, only bin/node.
ARG NODE_VERSION=26.12.1
ARG NODE_SHA256_ARM64=a6c01af459dd8972ab3fb08ee5da81244d26ba8c4944a62d5bbba8c597ae154f
ARG NODE_SHA256_X64=0fd1eb7770598f52a21995edf6ed35fc690400fa437c2386af329519e9143132
RUN case "$(dpkg --print-architecture)" in \
        arm64) arch=arm64; sum="${NODE_SHA256_ARM64}" ;; \
        amd64) arch=x64;   sum="${NODE_SHA256_X64}" ;; \
        *) echo "no Node build for $(dpkg --print-architecture)"; exit 1 ;; \
    esac \
    && curl -fsSL -o /tmp/node.tar.gz "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-${arch}.tar.gz" \
    && echo "${sum}  /tmp/node.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/node.tar.gz -C /usr/local/bin --strip-components=2 "node-v${NODE_VERSION}-linux-${arch}/bin/node" \
    && rm /tmp/node.tar.gz \
    && node --version

# box.mjs is CryptoCart's `npm run build:box -w apps/pos` at the commit in
# CHANGELOG.md; the build is reproducible, so this hash is that commit's.
COPY box/box.mjs /opt/nmt-box/box.mjs
RUN echo "709bbead2d6188ecef8afaaf47f03a3dd5ff6e4369a03ccb16154f75ff32f199  /opt/nmt-box/box.mjs" | sha256sum -c -

COPY agent/ /opt/nmt-print-agent/
COPY rootfs/ /

WORKDIR /opt/nmt-print-agent
CMD ["/usr/local/bin/nmt-stdin"]
