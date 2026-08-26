FROM node:20-alpine

WORKDIR /app

# Install dependencies first so this layer is cached across source changes.
COPY package*.json ./
RUN npm ci --omit=dev && npm cache clean --force

COPY . .

# Drop root before running the app.
RUN chown -R node:node /app
USER node

ENV NODE_ENV=production
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://localhost:3000/health || exit 1

CMD ["node", "app.js"]
