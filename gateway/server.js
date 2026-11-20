// API gateway de Randuin: la unica puerta interna del sistema (puerto 8080). Caddy le pasa todo lo que llega por HTTPS.
// Es el gateway Node del laboratorio L6 (modulo http de Node, sin dependencias) con lo que pidieron EP2 y EP3:
//   1. Reparte entre las 2 replicas de ia (round robin con las IP que entrega el DNS de Docker).
//   2. Exige la API key (header X-API-Key) en todas las rutas con datos; sin ella: 401. Sin API_KEY configurada nadie entra.
//   3. Limita las peticiones por cliente (rate limiting): pasado el limite, 429 con Retry-After.
//   4. No publica nunca /interno (las rutas que el worker usa para hablar con el dominio).
//   5. Escribe una linea por peticion (docker compose logs gateway): de ahi sale la metrica de errores 5xx.
//
// Rutas (EP3, 2.5):
//   /api/health, /docs, /openapi.json    publicas
//   /api/predict, /api/predicciones/{id}, /api/revision, /api/metricas     -> dominio, con X-API-Key
//   /api/decision/*                      -> dominio (rutas de EP2), con X-API-Key
//   /api/ia/*                            -> las 2 replicas de ia; solo /api/ia/health es publica
//   todo lo demas                        -> el front
const http = require('node:http');
const dns = require('node:dns');
const crypto = require('node:crypto');

// Sin valor por defecto: si falta, las rutas protegidas rechazan a todos (falla cerrada) en vez de aceptar una clave conocida.
const API_KEY = process.env.API_KEY || '';
// Peticiones por minuto y por cliente a /api/*. Para la demostracion del 429 se baja (p. ej. 30); para la prueba de carga se sube
// (el limite mediria el del gateway y no el del sistema): se cambia en el .env y se corre `docker compose up -d gateway`.
const LIMITE_POR_MINUTO = Number(process.env.LIMITE_POR_MINUTO || 120);

// [patron, servicio interno, puerto, conservar la ruta tal cual]. El orden importa: la ultima ("/") atrapa todo lo demas.
// Las rutas de EP2 (/api/ia, /api/decision) pierden su prefijo: /api/ia/predict -> /predict. Las de EP3 las define el dominio con su
// ruta completa (/api/predict), asi que se conservan.
const destinos = [
  [/^\/api\/ia(\/|\?|$)/, 'ia', 8000, false],
  [/^\/api\/decision(\/|\?|$)/, 'dominio', 8000, false],
  [/^\/api\/(health|predict|predicciones|revision|metricas)(\/|\?|$)/, 'dominio', 8000, true],
  [/^\/(docs|openapi\.json|redoc)(\/|\?|$)/, 'dominio', 8000, true],
  [/^\//, 'front', 80, true],
];

// Rutas de /api que NO piden clave: el estado de cada pieza (los chequeos y la comprobacion externa la necesitan abierta).
const PUBLICAS = [/^\/api\/ia\/health(\?|$)/, /^\/api\/decision\/health(\?|$)/, /^\/api\/health(\?|$)/];

function destinoDe(url) {
  return destinos.find(([ruta]) => ruta.test(url));
}

// /api/ia/predict -> /predict : cada microservicio no sabe con que prefijo lo publica el gateway.
function rutaInterna(url, servicio, conservar = servicio === 'front') {
  return conservar ? url : url.replace(/^\/api\/[a-z]+/, '') || '/';
}

// Todo lo que cuelga de /api exige clave, salvo el estado de las piezas. /docs y el front son publicos.
function necesitaClave(url) {
  return url.startsWith('/api/') && !PUBLICAS.some(p => p.test(url));
}

// /interno es de la red de Docker: lo usa el worker para entregar el texto del LLM al dominio. Desde afuera no existe.
function esInterna(url) {
  return /^\/interno(\/|\?|$)/.test(url);
}

// Se compara el hash de las dos claves (mismo largo siempre) en tiempo constante: no se puede adivinar la clave midiendo tiempos.
function claveValida(recibida, esperada = API_KEY) {
  if (!esperada || typeof recibida !== 'string' || !recibida) return false;
  const hash = texto => crypto.createHash('sha256').update(texto).digest();
  return crypto.timingSafeEqual(hash(recibida), hash(esperada));
}

// Rate limiting con "cubo de fichas" (token bucket): cada cliente tiene `porMinuto` fichas, cada peticion gasta una y se recargan de a
// poco. Permite rafagas cortas hasta el limite y despues exige el ritmo sostenido. El cliente es la IP real (Caddy la pone en
// X-Forwarded-For; a un header que ya traiga el cliente lo reemplaza, asi que no se puede falsear desde afuera).
class Limitador {
  constructor(porMinuto, ahora = () => Date.now()) {
    this.capacidad = Math.max(1, porMinuto);
    this.porMs = this.capacidad / 60000;
    this.ahora = ahora;
    this.cubos = new Map();
  }

  tomar(cliente) {
    const t = this.ahora();
    const cubo = this.cubos.get(cliente) || { fichas: this.capacidad, t };
    cubo.fichas = Math.min(this.capacidad, cubo.fichas + (t - cubo.t) * this.porMs);
    cubo.t = t;
    this.cubos.set(cliente, cubo);
    if (cubo.fichas >= 1) {
      cubo.fichas -= 1;
      return { ok: true, restantes: Math.floor(cubo.fichas) };
    }
    return { ok: false, restantes: 0, reintentarEnS: Math.max(1, Math.ceil((1 - cubo.fichas) / this.porMs / 1000)) };
  }

  // Borra a los clientes que llevan 2 minutos sin pedir nada: la memoria no crece sin limite.
  limpiar() {
    const t = this.ahora();
    for (const [cliente, cubo] of this.cubos) if (t - cubo.t > 120000) this.cubos.delete(cliente);
  }
}

function ipCliente(cliente) {
  const reenviada = cliente.headers['x-forwarded-for'];
  return (reenviada ? reenviada.split(',')[0].trim() : cliente.socket.remoteAddress) || 'desconocida';
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

function json(respuesta, codigo, cuerpo, cabeceras = {}) {
  respuesta.writeHead(codigo, { 'Content-Type': 'application/json', ...cabeceras });
  respuesta.end(JSON.stringify(cuerpo));
}

function crearServidor(opciones = {}) {
  const limitador = new Limitador(opciones.limitePorMinuto ?? LIMITE_POR_MINUTO, opciones.ahora);
  const resolver = opciones.elegirIp || elegirIp;
  const clave = opciones.apiKey === undefined ? API_KEY : opciones.apiKey;
  const limpieza = setInterval(() => limitador.limpiar(), 60000);
  limpieza.unref();

  const servidor = http.createServer((cliente, respuesta) => {
    const inicio = Date.now();
    let url;
    try {
      // new URL resuelve "." y ".." : /api/predict/../../interno/x queda /interno/x y se bloquea, no se cuela por el prefijo.
      const u = new URL(cliente.url, 'http://gateway');
      url = u.pathname + u.search;
    } catch {
      return json(respuesta, 400, { error: 'ruta invalida' });
    }
    const ip = ipCliente(cliente);

    if (url === '/health' && cliente.method === 'GET') {
      return json(respuesta, 200, { gateway: 'ok' });
    }
    if (esInterna(url)) {
      console.log(`${cliente.method} ${url} -> 404 ruta interna (${ip})`);
      return json(respuesta, 404, { error: 'no existe' });
    }
    if (url.startsWith('/api/')) {
      const permiso = limitador.tomar(ip);
      if (!permiso.ok) {
        console.log(`${cliente.method} ${url} -> 429 limite de ${limitador.capacidad}/min (${ip})`);
        return json(respuesta, 429,
          { error: `demasiadas peticiones: el limite es de ${limitador.capacidad} por minuto`, reintentar_en_s: permiso.reintentarEnS },
          { 'Retry-After': String(permiso.reintentarEnS) });
      }
    }
    const [, servicio, puerto, conservar] = destinoDe(url);
    if (necesitaClave(url) && !claveValida(cliente.headers['x-api-key'], clave)) {
      console.log(`${cliente.method} ${url} -> 401 sin API key valida (${ip})`);
      return json(respuesta, 401, { error: 'falta la API key (header X-API-Key) o no es valida' });
    }
    resolver(servicio, (error, direccion) => {
      const fallo = () => {
        console.log(`${cliente.method} ${url} -> ${servicio} 502 no responde (${Date.now() - inicio} ms)`);
        if (respuesta.headersSent) return respuesta.destroy();
        json(respuesta, 502, { error: `${servicio} no responde` });
      };
      if (error) return fallo();
      const solicitud = http.request({
        host: direccion,
        port: puerto,
        path: rutaInterna(url, servicio, conservar),
        method: cliente.method,
        headers: cliente.headers,
        timeout: 10000,
      }, interna => {
        respuesta.writeHead(interna.statusCode, interna.headers);
        interna.pipe(respuesta);
        console.log(`${cliente.method} ${url} -> ${servicio} ${direccion} ${interna.statusCode} (${Date.now() - inicio} ms)`);
      });
      solicitud.on('timeout', () => solicitud.destroy(new Error('timeout')));
      solicitud.on('error', fallo);
      cliente.pipe(solicitud);
    });
  });
  servidor.limitador = limitador;
  return servidor;
}

module.exports = { destinoDe, rutaInterna, necesitaClave, esInterna, claveValida, Limitador, ipCliente, crearServidor };

if (require.main === module) {
  if (!API_KEY) console.log('AVISO: falta API_KEY. Las rutas protegidas rechazaran a todos (401).');
  crearServidor().listen(8080, '0.0.0.0', () =>
    console.log(`Gateway Node listo en 8080 (limite: ${LIMITE_POR_MINUTO} peticiones por minuto y por cliente)`));
}
