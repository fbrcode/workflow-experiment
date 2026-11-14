# Golden-vector generator. Same Node version as the n8n 2.47.7 image (docker/docker-bake.hcl).
FROM node:26.13.0-alpine3.24

WORKDIR /golden
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY src/ ./src/

USER node
ENTRYPOINT ["node", "src/generate.mjs"]
