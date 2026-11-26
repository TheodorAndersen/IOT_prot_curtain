FROM node:22-alpine AS builder

WORKDIR /app
RUN npm install -g npm@12.2.3 --no-audit --no-fund
COPY package*.json ./
RUN npm install --no-audit --no-fund
COPY . .
RUN npm run build

FROM nginx:1.31.9-alpine
COPY --from=builder /app/dist /usr/share/nginx/html
