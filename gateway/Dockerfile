# Gateway: nginx oficial, version estable, variante alpine (imagen de ~50 MB).
FROM nginx:1.28-alpine
COPY nginx.conf /etc/nginx/nginx.conf
EXPOSE 8080
# Valida la configuracion al construir: si el nginx.conf tiene un error, el pipeline falla aqui.
RUN nginx -t
