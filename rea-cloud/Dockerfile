FROM node:24.11.1-bookworm-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends procps ca-certificates && rm -rf /var/lib/apt/lists/*
COPY rea-cloud/package.json ./package.json
RUN npm install --omit=dev --omit=optional
COPY rea-cloud/server.mjs ./server.mjs
ENV NODE_ENV=production
ENV PORT=10000
EXPOSE 10000
CMD ["node","server.mjs"]
