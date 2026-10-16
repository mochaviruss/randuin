// Test del gateway (mismo estilo que prueba.js del L4): si algo falla, termina con codigo 1 y el pipeline se detiene.
const { destinoDe, rutaInterna, necesitaClave } = require('./server.js');

const casos = [
  ['/api/ia/predict va a ia', destinoDe('/api/ia/predict')[1] === 'ia'],
  ['/api/decision/evaluar va a dominio', destinoDe('/api/decision/evaluar')[1] === 'dominio'],
  ['/ va al front', destinoDe('/')[1] === 'front'],
  ['se quita el prefijo /api/ia', rutaInterna('/api/ia/predict', 'ia') === '/predict'],
  ['se quita el prefijo /api/decision', rutaInterna('/api/decision/predicciones?limite=5', 'dominio') === '/predicciones?limite=5'],
  ['/predict exige API key', necesitaClave('/api/ia/predict') === true],
  ['/api/ia/health no exige API key', necesitaClave('/api/ia/health') === false],
  ['el dominio no exige API key', necesitaClave('/api/decision/evaluar') === false],
];

const fallidos = casos.filter(([, ok]) => !ok);
casos.forEach(([nombre, ok]) => console.log(`${ok ? 'ok   ' : 'FALLA'} ${nombre}`));
if (fallidos.length) {
  console.log(`FALLA ${fallidos.length}/${casos.length}`);
  process.exit(1);
}
console.log(`Las ${casos.length} pruebas pasaron`);
