FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production
COPY package.json package-lock.json ./
RUN npm ci --omit=dev
COPY src ./src
# node:sqlite needs the flag on 22.5-22.12 and ignores it afterwards
CMD ["node", "--experimental-sqlite", "src/index.js"]
