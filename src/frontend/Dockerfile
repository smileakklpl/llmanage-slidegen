# Stage 1: Build the React app
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Build-time env: API calls go to /api (same origin, Nginx will proxy)
ENV VITE_API_BASE_URL=""
RUN npm run build

# Stage 2: Serve with Nginx
FROM nginx:alpine

# Copy built files
COPY --from=build /app/dist /usr/share/nginx/html

# Copy nginx config
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
