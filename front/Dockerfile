# Front: tres archivos estaticos (HTML, CSS y JavaScript) servidos por nginx (imagen oficial, solo sirve archivos).
# La pagina llama a la API con rutas relativas (/api/...), asi todo pasa por el gateway. nginx.conf agrega los headers de seguridad.
FROM nginx:1.28-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html estilo.css app.js /usr/share/nginx/html/
EXPOSE 80
