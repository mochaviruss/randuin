// Test del front (mismo estilo que gateway/prueba.js): revisa la pagina antes de construir la imagen.
// Si algo falla, termina con codigo 1 y el pipeline se detiene.
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');

const casos = [
  ['la pagina tiene el formulario de evaluacion', html.includes('<form')],
  ['evalua con POST /api/decision/evaluar', html.includes("'/api/decision/evaluar'")],
  ['muestra las ultimas decisiones', html.includes('/api/decision/predicciones')],
  ['todo pasa por el gateway: sin localhost ni puertos internos', !/localhost|:8000|:5432/.test(html)],
];

const fallidos = casos.filter(([, ok]) => !ok);
casos.forEach(([nombre, ok]) => console.log(`${ok ? 'ok   ' : 'FALLA'} ${nombre}`));
if (fallidos.length) {
  console.log(`FALLA ${fallidos.length}/${casos.length}`);
  process.exit(1);
}
console.log(`Las ${casos.length} pruebas pasaron`);
