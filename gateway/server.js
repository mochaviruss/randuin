// API gateway de Randuin: la unica puerta del sistema (puerto 8080).
// Es el gateway Node del laboratorio L6 (modulo http de Node, sin dependencias) con tres agregados que pide la EP2:
//   1. Reparte entre las 2 replicas de ia (round robin con las IP que entrega el DNS de Docker).
//   2. Exige una API key para llamar al modelo (D8: autenticacion implementada en EP2).
//   3. Escribe una linea por peticion (docker compose logs gateway): de ahi sale la metrica de errores 5xx (8.1).
const http = require('node:http');
const dns = require('node:dns');

const API_KEY = process.env.API_KEY || 'randuin-local';   // valor de ejemplo; en produccion, un gestor de secretos

// Ruta publica -> servicio interno y su puerto. El orden importa: la ultima ("/") atrapa todo lo demas.
const destinos = [
  [/^\/api\/ia(\/|$)/, 'ia', 8000],
  [/^\/api\/decision(\/|$)/, 'dominio', 8000],
  [/^\//, 'front', 80],
];

function destinoDe(url) {
  return destinos.find(([ruta]) => ruta.test(url));
}

// /api/ia/predict -> /predict : cada microservicio no sabe con que prefijo lo publica el gateway.
function rutaInterna(url, servicio) {
  return servicio === 'front' ? url : url.replace(/^\/api\/[a-z]+/, '') || '/';
}

// Al modelo solo se llega con la API key (la conoce el dominio). /api/ia/health queda abierta para los chequeos.
function necesitaClave(url) {
  return url.startsWith('/api/ia') && !url.startsWith('/api/ia/health');
}

// Round robin: Docker resuelve "ia" a una IP por replica; se van turnando. Si una replica se detiene,
// Docker la saca del DNS y las peticiones siguientes van a la otra.
let turno = 0;
function elegirIp(servicio, listo) {
  dns.lookup(servicio, { all: true, family: 4 }, (error, ips) => {
    if (error) return listo(error);
    listo(null, ips[turno++ % ips.length].address);
  });
}

function json(respuesta, codigo, cuerpo) {
  respuesta.writeHead(codigo, { 'Content-Type': 'application/json' });
  respuesta.end(JSON.stringify(cuerpo));
}

const servidor = http.createServer((cliente, respuesta) => {
  const inicio = Date.now();
  if (cliente.url === '/health' && cliente.method === 'GET') {
    return json(respuesta, 200, { gateway: 'ok' });
  }
  const [, servicio, puerto] = destinoDe(cliente.url);
  if (necesitaClave(cliente.url) && cliente.headers['x-api-key'] !== API_KEY) {
    console.log(`${cliente.method} ${cliente.url} -> 401 sin API key`);
    return json(respuesta, 401, { error: 'falta la API key (header X-API-Key) o no es valida' });
  }
  elegirIp(servicio, (error, ip) => {
    const fallo = () => {
      console.log(`${cliente.method} ${cliente.url} -> ${servicio} 502 no responde (${Date.now() - inicio} ms)`);
      if (respuesta.headersSent) return respuesta.destroy();
      json(respuesta, 502, { error: `${servicio} no responde` });
    };
    if (error) return fallo();
    const solicitud = http.request({
      host: ip,
      port: puerto,
      path: rutaInterna(cliente.url, servicio),
      method: cliente.method,
      headers: cliente.headers,
      timeout: 10000,
    }, interna => {
      respuesta.writeHead(interna.statusCode, interna.headers);
      interna.pipe(respuesta);
      console.log(`${cliente.method} ${cliente.url} -> ${servicio} ${ip} ${interna.statusCode} (${Date.now() - inicio} ms)`);
    });
    solicitud.on('timeout', () => solicitud.destroy(new Error('timeout')));
    solicitud.on('error', fallo);
    cliente.pipe(solicitud);
  });
});

module.exports = { destinoDe, rutaInterna, necesitaClave };

if (require.main === module) {
  servidor.listen(8080, '0.0.0.0', () => console.log('Gateway Node listo en 8080'));
}
