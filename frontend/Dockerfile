# ── Stage 1: Build ────────────────────────────────────────────────────────────
FROM node:22-alpine AS builder

WORKDIR /app

# Install dependencies first (layer-cached separately from source)
COPY package.json package-lock.json* ./
# Match the npm resolver used to validate the frontend lockfile in CI.
RUN npm install --global npm@11.19.1
RUN npm ci --silent

# Copy source and build
COPY . .
RUN npm run build && npm run test:css
# Output is in /app/dist


# ── Stage 2: Serve with nginx ──────────────────────────────────────────────────
FROM nginx:1.27-alpine AS runner

# nginx config with API proxy
ENV PORT=80 API_UPSTREAM=http://api:8000
# Read the runtime DNS servers from /etc/resolv.conf, which is important on
# Railway where a private service's IP can change after a backend redeploy.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1
# Keep nginx variables ($request_uri, $proxy_host, etc.) intact in the template.
ENV NGINX_ENVSUBST_FILTER="^(PORT|API_UPSTREAM|NGINX_LOCAL_RESOLVERS)$"
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Built static files
COPY --from=builder /app/dist /usr/share/nginx/html

# Create a non-root user for nginx worker processes
RUN chown -R nginx:nginx /usr/share/nginx/html && \
    chown -R nginx:nginx /var/cache/nginx && \
    touch /var/run/nginx.pid && \
    chown nginx:nginx /var/run/nginx.pid

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
