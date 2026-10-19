FROM node:24.11.1-bookworm-slim AS node

FROM eclipse-temurin:21-jdk-jammy

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    procps \
    ca-certificates \
    curl \
    unzip \
    xz-utils \
    file \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node /usr/local /usr/local

WORKDIR /app

COPY rea-cloud/package.json ./package.json
RUN npm install --omit=dev

ARG GHIDRA_VERSION=12.1.4
ARG GHIDRA_ARCHIVE=ghidra_12.1.4_PUBLIC_20260921.zip
ARG GHIDRA_URL=https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.4_build/ghidra_12.1.4_PUBLIC_20260921.zip
ARG GHIDRA_SHA256=ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db

RUN curl -fL --retry 4 --retry-delay 2 -o /tmp/${GHIDRA_ARCHIVE} "${GHIDRA_URL}" \
    && echo "${GHIDRA_SHA256}  /tmp/${GHIDRA_ARCHIVE}" | sha256sum -c - \
    && unzip -q /tmp/${GHIDRA_ARCHIVE} -d /opt \
    && rm -f /tmp/${GHIDRA_ARCHIVE} \
    && test -x /opt/ghidra_12.1.4_PUBLIC/support/analyzeHeadless

ARG JADX_URL=https://github.com/1013503897/jadx-headless-mcp/releases/download/v0.7.1/jadx-headless-mcp-0.7.1-all.jar
ARG JADX_SHA256=6e5eacf500b64292bfb73c49797c1958f6ee44646e43e868039ae7feb573ff75

RUN mkdir -p /opt/rea/android \
    && curl -fL --retry 4 --retry-delay 2 -o /opt/rea/android/jadx-headless-mcp-0.7.1-all.jar "${JADX_URL}" \
    && echo "${JADX_SHA256}  /opt/rea/android/jadx-headless-mcp-0.7.1-all.jar" | sha256sum -c -

COPY rea-cloud/server.mjs ./server.mjs

ENV NODE_ENV=production
ENV PORT=10000
ENV JAVA_HOME=/opt/java/openjdk
ENV GHIDRA_INSTALL_DIR=/opt/ghidra_12.1.4_PUBLIC
ENV GHIDRA_HEADLESS_MAXMEM=256M
ENV REA_GHIDRA_STARTUP_TIMEOUT_MS=330000
ENV REA_JADX_MCP_JAR=/opt/rea/android/jadx-headless-mcp-0.7.1-all.jar
ENV REA_JADX_HEAP_MIB=256
ENV REA_JADX_ACTIVE_PROCESSOR_COUNT=1

RUN node --version \
    && java -version \
    && ./node_modules/.bin/rea --version \
    && node -e "import('frida').then(()=>console.log('frida 17.23.2 loaded')).catch(e=>{console.error(e);process.exit(1)})" \
    && test -x "${GHIDRA_INSTALL_DIR}/support/analyzeHeadless" \
    && test -f "${REA_JADX_MCP_JAR}"

EXPOSE 10000

CMD ["node","server.mjs"]
