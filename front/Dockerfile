# Front: una pagina HTML estatica servida por nginx (imagen oficial, solo sirve archivos).
# La pagina llama a la API con rutas relativas (/api/decision/...), asi todo pasa por el gateway.
FROM nginx:1.28-alpine
COPY index.html /usr/share/nginx/html/index.html
EXPOSE 80
