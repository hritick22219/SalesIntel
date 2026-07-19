# Build Stage
FROM node:20-alpine AS build

WORKDIR /app

# Copy package descriptors and install dependencies
COPY package*.json ./
RUN npm install

# Copy frontend source files
COPY . .

# Build argument for VITE_API_URL so it can be set at build-time
ARG VITE_API_URL
ENV VITE_API_URL=$VITE_API_URL

# Build the static site
RUN npm run build

# Production Stage
FROM nginx:alpine

# Copy built assets to Nginx html directory
COPY --from=build /app/dist /usr/share/nginx/html

# Copy custom Nginx configuration for routing
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
