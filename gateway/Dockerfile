# Gateway Node, igual que en el laboratorio L6: solo el modulo http de Node, sin dependencias.
FROM node:22-alpine
WORKDIR /app
COPY server.js .
USER node
EXPOSE 8080
CMD ["node", "server.js"]
