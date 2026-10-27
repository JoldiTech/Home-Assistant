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

# Node for CryptoCart's register box: the nodejs.org build, only bin/node.
ARG NODE_VERSION=24.22.1
ARG NODE_SHA256_ARM64=33c37be234ba8bee041961047b19160f4d7d0f164e8e246f78bdd10feff148b3
ARG NODE_SHA256_X64=eb6728e77c576a35162c122c45ccadf224d89ff00abc8dae46cc2897c6002d7c
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
RUN echo "06e793b6764156c3ce44a220803cd8dfdbc419706d71cb48f97ebf87f34e9db0  /opt/nmt-box/box.mjs" | sha256sum -c -

COPY agent/ /opt/nmt-print-agent/
COPY rootfs/ /

WORKDIR /opt/nmt-print-agent
CMD ["/usr/local/bin/nmt-stdin"]
