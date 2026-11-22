// Test del front (mismo estilo que gateway/prueba.js): revisa la pagina antes de construir la imagen.
// Si algo falla, termina con codigo 1 y el pipeline se detiene. Tres partes: lo que la pagina promete (rutas, seguridad, celular),
// la logica pura de app.js (sin navegador) y la imagen (Dockerfile y nginx).
const fs = require('fs');
const path = require('path');
const leer = nombre => fs.readFileSync(path.join(__dirname, nombre), 'utf8');

const html = leer('index.html');
const css = leer('estilo.css');
const js = leer('app.js');
const dockerfile = leer('Dockerfile');
const nginx = leer('nginx.conf');
const app = require('./app.js');

const casos = [];
const caso = (nombre, ok) => casos.push([nombre, Boolean(ok)]);

// ------------------------------------------------------------------------------------------------------- lo que la pagina promete
caso('la pagina tiene el formulario de evaluacion', html.includes('<form id="formulario"'));
caso('evalua con POST /api/predict', js.includes("'/api/predict'"));
caso('consulta el resumen en /api/predicciones/{id}', js.includes('/api/predicciones/'));
caso('muestra la cola en /api/revision', js.includes('/api/revision'));
caso('muestra el dashboard con /api/metricas', js.includes('/api/metricas'));
caso('muestra el estado de cada pieza con /api/health (publica, sin clave)', js.includes("'/api/health', { conClave: false }"));
caso('manda la clave en el header X-API-Key', js.includes("'X-API-Key'"));
caso('todo pasa por el gateway: sin localhost ni puertos internos', !/localhost|:8000|:5432|:5672/.test(html + js + css));
caso('no depende de nada de afuera: sin CDN ni http(s):// en HTML, CSS ni JS', !/https?:\/\//.test(html + js + css));
caso('tiene las tres secciones: evaluar, revision y dashboard', ['panel-evaluar', 'panel-revision', 'panel-dashboard'].every(id => html.includes(`id="${id}"`)));
// ------------------------------------------------------------------------------------------------------------ entrada con clave
caso('hay una pantalla de entrada con la clave en un campo oculto (type="password")', html.includes('id="entrada"') && /<input id="clave"[^>]*type="password"/.test(html));
caso('la aplicacion empieza oculta: no se muestra hasta que el servidor valide la clave', /<div class="aplicacion" id="aplicacion" hidden>/.test(html) && /id="salir"[^>]*hidden/.test(html));
caso('el atributo hidden se respeta aunque una clase defina display', css.includes('[hidden] { display: none !important; }'));
caso('la clave se valida contra una ruta protegida antes de abrir la sesion', /async function verificarClave\(\) \{[\s\S]*?api\('\/api\/metricas\?horas=1'\)[\s\S]*?mostrarSesion\(true\)/.test(js));
caso('una clave que no sirve no se conserva', /guardarClave\(''\);\s*mostrarSesion\(false, mensajeError/.test(js));
caso('"Salir" borra la clave y vuelve a la entrada', js.includes("$('salir').addEventListener('click', () => cerrarSesion())") && /function cerrarSesion[\s\S]*?guardarClave\(''\)/.test(js));
caso('un 401 con la sesion abierta devuelve a la entrada', /r\.status === 401 && document\.body\.classList\.contains\('con-sesion'\)\) cerrarSesion\(/.test(js));
caso('el campo de la clave puede mostrarse y ocultarse (se escribe en un celular)', html.includes('id="ver-clave"') && js.includes("$('clave').type = visible ? 'text' : 'password'"));
caso('explica que el LLM no decide', html.includes('LLM</b> solo redacta') || html.includes('solo redacta'));

// ----------------------------------------------------------------------------------------------------------------- seguridad
// Se mira el codigo, no los comentarios: el propio app.js explica en un comentario por que NO se usa innerHTML.
const codigoJs = js.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1');
caso('nunca arma HTML con datos del servidor: sin innerHTML, outerHTML, insertAdjacentHTML, document.write ni eval',
  !/innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function/.test(codigoJs));
caso('replaceChildren nunca recibe un null directo (se mostraria la palabra "null"): los dos con condiciones pasan por sinNulos',
  /contenedor\.replaceChildren\(\.\.\.sinNulos\(\[/.test(codigoJs) && /\$\('resultado'\)\.replaceChildren\(\.\.\.sinNulos\(\[/.test(codigoJs) &&
  /const sinNulos = lista => lista\.filter\(x => x !== null && x !== undefined && x !== false\)/.test(codigoJs));
caso('el detector de comentarios funciona (si app.js usara innerHTML en codigo, esta prueba lo veria)',
  /innerHTML/.test('x.innerHTML = y;'.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/.*$/gm, '$1')));
caso('sin scripts ni estilos incrustados en el HTML (lo exige el CSP)', !/<style/.test(html) && !/<script(?![^>]*\bsrc=)/.test(html) && !/\sstyle=/.test(html));
caso('la clave no esta escrita en el codigo ni en el HTML', !/randuin-local|clave-de-prueba|nvapi-/.test(html + js));
caso('la clave solo se guarda en la pestana (sessionStorage), no en localStorage', js.includes('sessionStorage') && !js.includes('localStorage'));
caso('el curl que se muestra usa un marcador, no la clave', js.includes('TU_CLAVE'));
caso('nginx manda un CSP que solo permite recursos propios', /Content-Security-Policy.*default-src 'self'.*script-src 'self'.*style-src 'self'/.test(nginx));
caso('nginx no deja enmarcar la pagina ni olfatear tipos', nginx.includes('X-Frame-Options') && nginx.includes('nosniff'));
caso('nginx abre index.html en "/" (try_files con $uri/): sin eso responde 404 y el healthcheck del compose falla', /try_files \$uri \$uri\/ =404;/.test(nginx));
caso('la imagen copia la pagina, los estilos, el script y la configuracion de nginx',
  ['index.html', 'estilo.css', 'app.js'].every(f => dockerfile.includes(f)) && dockerfile.includes('nginx.conf'));

// ------------------------------------------------------------------------------------------------------------------- celular
caso('declara el viewport para pantallas chicas', /<meta name="viewport" content="width=device-width, initial-scale=1/.test(html));
caso('respeta las zonas seguras de los celulares con muesca (viewport-fit y safe-area)', html.includes('viewport-fit=cover') && css.includes('env(safe-area-inset-bottom)'));
caso('en pantallas grandes cambia el diseno (media query de 720 px)', css.includes('@media (min-width: 720px)'));
caso('en el celular las pestanas quedan abajo, al alcance del pulgar', /\.pestanas \{[^}]*position: fixed;[^}]*bottom: 0/.test(css));
caso('los botones y campos tienen al menos 44 px de alto (se tocan con el dedo)', /min-height: 4[4-9]px|min-height: [5-9]\d+px|--alto-tab: 6\dpx/.test(css) && css.includes('min-height: 46px'));
caso('los campos usan letra de 16 px o mas (iOS no hace zoom al enfocarlos)', /input, select \{[^}]*font: inherit/.test(css) && /body \{[^}]*font: 16px/.test(css));
caso('los campos numericos abren el teclado numerico', html.includes('inputmode="decimal"'));
caso('respeta el modo oscuro y el movimiento reducido', css.includes('prefers-color-scheme: dark') && css.includes('prefers-reduced-motion'));
caso('el color no es la unica pista: la decision lleva icono y texto', js.includes("icono: '✔'") && js.includes("icono: '✖'"));

// ------------------------------------------------------------------------------------------------------- la logica de app.js
caso('infoDecision: aprobar, revisar y bloquear', app.infoDecision('aprobar').clase === 'ok' && app.infoDecision('revisar').clase === 'aviso' && app.infoDecision('bloquear').clase === 'peligro');
caso('infoDecision: una decision desconocida no rompe la pagina', app.infoDecision('rara').texto === 'rara');

caso('mensajeError 401 manda a revisar la clave', /clave/.test(app.mensajeError(401, null).titulo));
caso('mensajeError 422 lista cada campo y su motivo, sin el prefijo body',
  JSON.stringify(app.mensajeError(422, { detail: [{ loc: ['body', 'monto'], msg: 'Input should be greater than 0' }] }).detalles) === '["monto: Input should be greater than 0"]');
caso('mensajeError 429 dice cuanto esperar (Retry-After)', /Reintenta en 7 s/.test(app.mensajeError(429, null, '7').titulo));
caso('mensajeError 0 es falta de conexion', /conexión/.test(app.mensajeError(0, null).titulo));
caso('mensajeError 503 no culpa al usuario', /no pudo responder/.test(app.mensajeError(503, null).titulo));
caso('mensajeError 404 muestra el detalle del servidor', app.mensajeError(404, { detail: 'el usuario X no existe' }).titulo === 'el usuario X no existe');

const curl = app.armarCurl('https://ejemplo.sslip.io', { usuario_id: "KP'1", monto: 10 });
caso('armarCurl apunta a /api/predict con el header de clave y un marcador', curl.includes('https://ejemplo.sslip.io/api/predict') && curl.includes("X-API-Key: TU_CLAVE"));
caso('armarCurl escapa las comillas simples (el comando sigue siendo valido)', curl.includes("KP'\\''1"));

caso('infoResumen pendiente: dice que se esta redactando y que la decision ya esta tomada', app.infoResumen({ estado: 'pendiente' }).clase === 'cargando' && /ya está tomada/.test(app.infoResumen({ estado: 'pendiente' }).texto));
caso('infoResumen listo: muestra el texto tal cual', app.infoResumen({ estado: 'listo', texto: 'Se bloqueó por intentos repetidos.' }).texto === 'Se bloqueó por intentos repetidos.');
const degradado = app.infoResumen({ estado: 'degradado', motivo: 'HTTP 401' });
caso('infoResumen degradado: da el motivo y aclara que la decision no cambia', degradado.clase === 'degradado' && /HTTP 401/.test(degradado.texto) && /no se ve afectada/.test(degradado.texto));
caso('infoResumen no_aplica: una compra aprobada no lleva resumen', /no llevan resumen/.test(app.infoResumen({ estado: 'no_aplica' }).texto));

caso('porcentaje redondea a un decimal y no divide por cero', app.porcentaje(1, 3) === 33.3 && app.porcentaje(5, 0) === 0);
caso('diaCorto pasa 2026-11-19 a 19/11', app.diaCorto('2026-11-19') === '19/11');
caso('numero muestra — cuando no hay dato', app.numero(null) === '—' && app.numero(undefined) === '—' && app.numero(5) === '5');

const cuerpo = app.cuerpoDeFormulario({ usuario_id: ' KP1 ', monto: '12000', moneda: 'CLP', canal: 'web', pais_transaccion: 'cl', dispositivo_id: '', comercio_id: ' CM1 ', fecha_hora: '' });
caso('cuerpoDeFormulario limpia, convierte el monto y pone el pais en mayusculas', cuerpo.usuario_id === 'KP1' && cuerpo.monto === 12000 && cuerpo.pais_transaccion === 'CL');
caso('cuerpoDeFormulario no envia los opcionales vacios', !('dispositivo_id' in cuerpo) && !('fecha_hora' in cuerpo) && cuerpo.comercio_id === 'CM1');
caso('cuerpoDeFormulario no puede llevar una tarjeta aunque alguien la escriba', !('tarjeta' in app.cuerpoDeFormulario({ usuario_id: 'a', monto: 1, tarjeta: '4111111111111111' })));

const fallidos = casos.filter(([, ok]) => !ok);
casos.forEach(([nombre, ok]) => console.log(`${ok ? 'ok   ' : 'FALLA'} ${nombre}`));
if (fallidos.length) {
  console.log(`FALLA ${fallidos.length}/${casos.length}`);
  process.exit(1);
}
console.log(`Las ${casos.length} pruebas pasaron`);
