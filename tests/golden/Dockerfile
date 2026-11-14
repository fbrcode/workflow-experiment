# Golden-vector generator. Same Node version as the production n8n 2.40.3 image (docker/docker-bake.hcl @ n8n@2.40.3).
FROM node:26.7.0-alpine3.24

WORKDIR /golden
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund
COPY src/ ./src/

USER node
ENTRYPOINT ["node", "src/generate.mjs"]
