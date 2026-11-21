// Test del gateway (mismo estilo que prueba.js del L4): si algo falla, termina con codigo 1 y el pipeline se detiene.
// Tres partes: el enrutamiento (sin red), el limitador de peticiones (con un reloj falso) y el servidor de verdad (peticiones HTTP
// reales; la red de los servicios internos se reemplaza por un resolutor que falla, asi un 502 significa "la ruta paso todos los
// controles y se intento enviar" y un 401, 404 o 429 significa "el gateway la corto antes").
const { destinoDe, rutaInterna, necesitaClave, esInterna, claveValida, Limitador, crearServidor } = require('./server.js');

const CLAVE = 'clave-de-prueba';
const sinRed = (_servicio, listo) => listo(new Error('sin red en el test'));
const casos = [];
const caso = (nombre, ok) => casos.push([nombre, Boolean(ok)]);

// ---------------------------------------------------------------------------------------------------------- enrutamiento
caso('/api/ia/predict va a ia', destinoDe('/api/ia/predict')[1] === 'ia');
caso('/api/decision/evaluar va a dominio', destinoDe('/api/decision/evaluar')[1] === 'dominio');
caso('/ va al front', destinoDe('/')[1] === 'front');
for (const ruta of ['/api/predict', '/api/predicciones/TX-1', '/api/revision?limite=3', '/api/metricas', '/api/health', '/docs', '/openapi.json']) {
  caso(`${ruta} va al dominio y conserva su ruta`, destinoDe(ruta)[1] === 'dominio' && rutaInterna(ruta, 'dominio', destinoDe(ruta)[3]) === ruta);
}
caso('se quita el prefijo /api/ia', rutaInterna('/api/ia/predict', 'ia') === '/predict');
caso('se quita el prefijo /api/decision', rutaInterna('/api/decision/predicciones?limite=5', 'dominio') === '/predicciones?limite=5');

// --------------------------------------------------------------------------------------------------------------- clave
for (const ruta of ['/api/ia/predict', '/api/predict', '/api/predicciones/TX-1', '/api/revision', '/api/metricas', '/api/decision/evaluar', '/api/decision/umbrales']) {
  caso(`${ruta} exige API key`, necesitaClave(ruta) === true);
}
for (const ruta of ['/api/health', '/api/ia/health', '/api/decision/health', '/docs', '/openapi.json', '/']) {
  caso(`${ruta} no exige API key`, necesitaClave(ruta) === false);
}
caso('una clave correcta es valida', claveValida(CLAVE, CLAVE));
caso('una clave distinta no es valida', !claveValida('otra', CLAVE) && !claveValida('clave-de-prueba ', CLAVE));
caso('sin clave o vacia no es valida', !claveValida(undefined, CLAVE) && !claveValida('', CLAVE));
caso('sin API_KEY configurada nadie entra (falla cerrada)', !claveValida('cualquiera', '') && !claveValida('', ''));

// ------------------------------------------------------------------------------------------------ rutas internas y trucos
caso('/interno es interna', esInterna('/interno/predicciones/TX-1/llm') && esInterna('/interno'));
caso('/internos o /api/interno no son /interno', !esInterna('/internos') && !esInterna('/api/interno'));

// ------------------------------------------------------------------------------------------------------------- limitador
{
  let t = 0;
  const l = new Limitador(5, () => t);                       // 5 por minuto = una ficha cada 12 s
  const primeras = [1, 2, 3, 4, 5].map(() => l.tomar('a').ok);
  const sexta = l.tomar('a');
  caso('las 5 primeras peticiones pasan y la sexta no', primeras.every(Boolean) && sexta.ok === false);
  caso('el rechazo dice cuanto esperar', sexta.reintentarEnS >= 1 && sexta.reintentarEnS <= 12);
  caso('otro cliente tiene su propio cubo', l.tomar('b').ok === true);
  t += 12000;                                                // pasaron 12 s: se recargo una ficha
  caso('a los 12 s se recarga una ficha', l.tomar('a').ok === true && l.tomar('a').ok === false);
  t += 600000;
  l.limpiar();
  caso('los clientes inactivos se borran', l.cubos.size === 0);
}

// -------------------------------------------------------------------------------------------------- el servidor de verdad
async function servidorDePrueba(opciones, prueba) {
  const servidor = crearServidor({ elegirIp: sinRed, apiKey: CLAVE, ...opciones });
  await new Promise(listo => servidor.listen(0, '127.0.0.1', listo));
  const base = `http://127.0.0.1:${servidor.address().port}`;
  try {
    await prueba(base);
  } finally {
    servidor.close();
  }
}
const pedir = (base, ruta, { clave, ip, metodo = 'GET' } = {}) =>
  fetch(base + ruta, { method: metodo, headers: { ...(clave ? { 'X-API-Key': clave } : {}), ...(ip ? { 'X-Forwarded-For': ip } : {}) } });

async function pruebasHttp() {
  await servidorDePrueba({ limitePorMinuto: 1000 }, async base => {
    caso('GET /health responde 200', (await pedir(base, '/health')).status === 200);
    // Prueba 3 de EP3: sin clave, 401
    for (const ruta of ['/api/predict', '/api/predicciones/TX-1', '/api/revision', '/api/metricas', '/api/ia/predict', '/api/decision/evaluar']) {
      caso(`${ruta} sin clave responde 401`, (await pedir(base, ruta, { metodo: ruta.includes('predict') || ruta.includes('evaluar') ? 'POST' : 'GET' })).status === 401);
    }
    caso('con una clave equivocada responde 401', (await pedir(base, '/api/predict', { clave: 'mala', metodo: 'POST' })).status === 401);
    caso('con la clave correcta la ruta pasa los controles (502: no hay red en el test)', (await pedir(base, '/api/predict', { clave: CLAVE, metodo: 'POST' })).status === 502);
    caso('/api/health es publica', (await pedir(base, '/api/health')).status === 502);
    caso('/docs es publica', (await pedir(base, '/docs')).status === 502);
    // /interno nunca sale, ni directo ni con trucos de ruta
    for (const ruta of ['/interno/predicciones/TX-1/llm', '/api/predict/../../interno/predicciones/TX-1/llm', '/api/health/../../interno/x']) {
      caso(`${ruta} responde 404`, (await pedir(base, ruta, { clave: CLAVE })).status === 404);
    }
  });

  // Prueba 4 de EP3: una rafaga sobre el limite responde 429
  await servidorDePrueba({ limitePorMinuto: 5 }, async base => {
    const estados = [];
    let ultima;
    for (let i = 0; i < 9; i++) {
      ultima = await pedir(base, '/api/predict', { clave: CLAVE, metodo: 'POST' });
      estados.push(ultima.status);
    }
    caso('en una rafaga de 9 con limite 5, las 5 primeras pasan y las 4 siguientes dan 429', estados.slice(0, 5).every(e => e === 502) && estados.slice(5).every(e => e === 429));
    const cuerpo = await ultima.json();
    caso('el 429 trae Retry-After y el motivo', Number(ultima.headers.get('retry-after')) >= 1 && /demasiadas peticiones/.test(cuerpo.error) && cuerpo.reintentar_en_s >= 1);
    caso('otro cliente (otra IP) no se ve afectado', (await pedir(base, '/api/predict', { clave: CLAVE, ip: '203.0.113.7', metodo: 'POST' })).status === 502);
    caso('el limite tambien protege contra adivinar la clave', (await pedir(base, '/api/predict', { clave: 'adivinando', metodo: 'POST' })).status === 429);
    caso('/health del gateway no cuenta para el limite', (await pedir(base, '/health')).status === 200);
  });

  // Con el cubo vacio, un cliente legitimo que espera se recupera
  let t = 0;
  await servidorDePrueba({ limitePorMinuto: 2, ahora: () => t }, async base => {
    await pedir(base, '/api/health'); await pedir(base, '/api/health');
    caso('agotadas las fichas responde 429', (await pedir(base, '/api/health')).status === 429);
    t += 30000;
    caso('pasado un rato vuelve a responder', (await pedir(base, '/api/health')).status === 502);
  });
}

pruebasHttp().then(() => {
  const fallidos = casos.filter(([, ok]) => !ok);
  casos.forEach(([nombre, ok]) => console.log(`${ok ? 'ok   ' : 'FALLA'} ${nombre}`));
  if (fallidos.length) {
    console.log(`FALLA ${fallidos.length}/${casos.length}`);
    process.exit(1);
  }
  console.log(`Las ${casos.length} pruebas pasaron`);
  process.exit(0);
}).catch(error => {
  console.log('ERROR en la prueba:', error);
  process.exit(1);
});
