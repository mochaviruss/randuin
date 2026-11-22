'use strict';
/* Front de Randuin (EP3). Dos partes:
     1. La parte PURA (formatos, mensajes de error, curl, estado del resumen): no toca el navegador y se prueba en Node (prueba.js).
     2. La parte del NAVEGADOR: arma la pantalla, llama a la API y muestra el resultado.
   Regla de oro de la segunda: todo lo que viene del servidor (razones, resumen del LLM, motivos) se escribe como TEXTO (textContent o
   createTextNode), nunca con innerHTML. El resumen lo redacta un LLM: tratarlo como HTML seria abrir la puerta a que alguien lo use
   para inyectar codigo. */

// ------------------------------------------------------------------------------------------------------------ parte pura
const DECISIONES = {
  aprobar: { texto: 'Aprobar', icono: '✔', clase: 'ok' },
  revisar: { texto: 'Revisar', icono: '!', clase: 'aviso' },
  bloquear: { texto: 'Bloquear', icono: '✖', clase: 'peligro' },
};

const ORIGENES = {
  modelo: 'Lo decidió el modelo de SageMaker.',
  lista_negra: 'Regla dura: el dispositivo o el comercio está en la lista negra. El modelo no se consultó.',
  reserva: 'Política de reserva: el modelo no respondió a tiempo, así que decidieron reglas simples.',
};

const PIEZAS = { base: 'Base', sagemaker: 'Modelo', cola: 'Cola', llm: 'LLM' };

function infoDecision(decision) {
  return DECISIONES[decision] || { texto: String(decision), icono: '?', clase: 'nulo' };
}

// Un mensaje claro para cada error que puede devolver el sistema. `datos` es el cuerpo JSON (o null).
function mensajeError(estado, datos, reintentarEn) {
  if (estado === 0) return { titulo: 'No hay conexión con el servidor.', detalles: [] };
  if (estado === 401) return { titulo: 'La clave no es válida o venció. Vuelve a ingresarla.', detalles: [] };
  if (estado === 422) {
    const detalles = Array.isArray(datos && datos.detail)
      ? datos.detail.map(e => `${(e.loc || []).filter(x => x !== 'body').join('.') || 'entrada'}: ${e.msg}`)
      : [typeof (datos && datos.detail) === 'string' ? datos.detail : 'la entrada no cumple el contrato'];
    return { titulo: 'Hay datos que corregir:', detalles };
  }
  if (estado === 404) return { titulo: (datos && typeof datos.detail === 'string') ? datos.detail : 'No se encontró lo pedido.', detalles: [] };
  if (estado === 429) {
    const s = Number(reintentarEn) || (datos && datos.reintentar_en_s) || 'unos';
    return { titulo: `Demasiadas peticiones seguidas. Reintenta en ${s} s.`, detalles: [] };
  }
  if (estado >= 500) return { titulo: `El sistema no pudo responder (error ${estado}). Intenta de nuevo en un momento.`, detalles: [] };
  return { titulo: `Error ${estado}.`, detalles: [] };
}

// El mismo POST como comando curl, para la demostracion. La clave NUNCA se imprime: va el marcador TU_CLAVE.
function armarCurl(origen, cuerpo) {
  const json = JSON.stringify(cuerpo).replace(/'/g, "'\\''");
  return `curl -s -X POST ${origen}/api/predict \\\n  -H 'Content-Type: application/json' \\\n  -H 'X-API-Key: TU_CLAVE' \\\n  -d '${json}'`;
}

// Como se muestra el estado del resumen del LLM (el texto de cada caso).
function infoResumen(llm) {
  const estado = llm && llm.estado;
  if (estado === 'pendiente') return { clase: 'cargando', titulo: 'Redactando el resumen para el analista…', texto: 'Llega después de la decisión, por una cola. Mientras tanto, la decisión ya está tomada.' };
  if (estado === 'listo') return { clase: 'listo', titulo: 'Resumen para el analista', texto: llm.texto || '' };
  if (estado === 'degradado') {
    return { clase: 'degradado', titulo: 'Resumen no disponible (modo degradado)',
             texto: `${llm.motivo ? `Motivo: ${llm.motivo}. ` : ''}La decisión de arriba no se ve afectada: la tomó el modelo.` };
  }
  return { clase: 'vacio', titulo: 'Compra aprobada', texto: 'Las compras aprobadas no llevan resumen.' };
}

function porcentaje(n, total) {
  return total ? Math.round((1000 * n) / total) / 10 : 0;
}

// "2026-11-19" -> "19/11"
function diaCorto(iso) {
  const [, mes, dia] = String(iso).split('-');
  return `${dia}/${mes}`;
}

function numero(n, decimales = 0) {
  return n === null || n === undefined ? '—' : Number(n).toLocaleString('es-CL', { maximumFractionDigits: decimales });
}

// El cuerpo de POST /api/predict a partir de los campos del formulario. Los opcionales vacios no se envian.
function cuerpoDeFormulario(campos) {
  const cuerpo = {
    usuario_id: String(campos.usuario_id || '').trim(),
    monto: Number(campos.monto),
    moneda: campos.moneda || 'CLP',
    canal: campos.canal || 'app',
    pais_transaccion: String(campos.pais_transaccion || 'CL').trim().toUpperCase(),
  };
  for (const campo of ['dispositivo_id', 'comercio_id', 'fecha_hora']) {
    const valor = String(campos[campo] || '').trim();
    if (valor) cuerpo[campo] = valor;
  }
  return cuerpo;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { DECISIONES, ORIGENES, infoDecision, mensajeError, armarCurl, infoResumen, porcentaje, diaCorto, numero, cuerpoDeFormulario };
}

// ------------------------------------------------------------------------------------------------------ parte del navegador
if (typeof document !== 'undefined') {
  const $ = id => document.getElementById(id);

  // Crea un elemento. Los hijos de tipo texto se agregan como texto: no hay forma de que el contenido se interprete como HTML.
  function h(etiqueta, atributos, ...hijos) {
    const el = document.createElement(etiqueta);
    for (const [k, v] of Object.entries(atributos || {})) {
      if (v === false || v === null || v === undefined) continue;
      if (k === 'class') el.className = v;
      else el.setAttribute(k, v === true ? '' : v);
    }
    for (const hijo of hijos.flat()) if (hijo !== null && hijo !== undefined && hijo !== false) el.append(hijo);
    return el;
  }

  // replaceChildren convierte un null en el TEXTO "null" (h() ya los descarta, pero aqui se le pasan directo): se filtran antes.
  const sinNulos = lista => lista.filter(x => x !== null && x !== undefined && x !== false);

  // ---- la clave: solo en esta pestaña (sessionStorage); si el navegador no la deja guardar, funciona igual mientras la pagina siga abierta
  let claveEnMemoria = '';
  const leerClave = () => { try { return sessionStorage.getItem('randuin_clave') || claveEnMemoria; } catch { return claveEnMemoria; } };
  const guardarClave = valor => {
    claveEnMemoria = valor;
    try { valor ? sessionStorage.setItem('randuin_clave', valor) : sessionStorage.removeItem('randuin_clave'); } catch { /* sin almacenamiento */ }
  };

  async function api(ruta, { metodo = 'GET', cuerpo, conClave = true } = {}) {
    const cabeceras = {};
    if (cuerpo) cabeceras['Content-Type'] = 'application/json';
    if (conClave) cabeceras['X-API-Key'] = leerClave();
    let r;
    try {
      r = await fetch(ruta, { method: metodo, headers: cabeceras, body: cuerpo ? JSON.stringify(cuerpo) : undefined });
    } catch {
      return { ok: false, estado: 0, datos: null, reintentarEn: null };
    }
    let datos = null;
    try { datos = await r.json(); } catch { /* sin cuerpo JSON */ }
    // Con la sesion abierta, un 401 significa que la clave dejo de servir (cambio en el servidor): se vuelve a la pantalla de entrada.
    if (conClave && r.status === 401 && document.body.classList.contains('con-sesion')) cerrarSesion('La clave ya no es válida. Ingrésala de nuevo.');
    return { ok: r.ok, estado: r.status, datos, reintentarEn: r.headers.get('retry-after') };
  }

  const pausa = ms => new Promise(listo => setTimeout(listo, ms));
  const hayClave = () => Boolean(leerClave());

  function cajaError(r) {
    const m = mensajeError(r.estado, r.datos, r.reintentarEn);
    return h('div', { class: 'error-caja', role: 'alert' }, m.titulo, m.detalles.length ? h('ul', {}, m.detalles.map(d => h('li', {}, d))) : null);
  }

  // ---------------------------------------------------------------------------------------------- estado de cada pieza
  async function actualizarSalud() {
    const r = await api('/api/health', { conClave: false });
    const lista = $('piezas');
    if (!r.datos || !r.datos.piezas) {
      lista.replaceChildren(h('li', { class: 'pieza error' }, '✖ sin conexión'));
      return;
    }
    lista.replaceChildren(...Object.entries(r.datos.piezas).map(([nombre, p]) => {
      const clase = p.estado === 'ok' ? 'ok' : p.estado === 'sin datos' ? 'nulo' : p.estado === 'degradado' ? 'aviso' : 'error';
      const icono = clase === 'ok' ? '✔' : clase === 'nulo' ? '–' : clase === 'aviso' ? '!' : '✖';
      return h('li', { class: `pieza ${clase}`, title: p.detalle || '' }, `${icono} ${PIEZAS[nombre] || nombre}`);
    }));
    const version = r.datos.piezas.sagemaker && r.datos.piezas.sagemaker.modelo_version;
    $('version-modelo').textContent = version ? `Modelo en producción: ${version}` : '';
  }

  // ------------------------------------------------------------------------------------------------------ entrada y sesion
  // Sesion = la clave ya fue validada por el servidor. Sin sesion solo se ve la pantalla de entrada; con sesion, la aplicacion.
  function mostrarSesion(activa, mensaje, clase) {
    document.body.classList.toggle('con-sesion', activa);
    $('entrada').hidden = activa;
    $('aplicacion').hidden = !activa;
    $('salir').hidden = !activa;
    const estado = $('estado-clave');
    estado.className = `estado-clave${clase ? ` ${clase}` : ''}`;
    estado.textContent = mensaje || '';
  }

  // Le pregunta al servidor si la clave sirve (una ruta protegida y barata). Una clave que no sirve no se conserva.
  async function verificarClave() {
    if (!hayClave()) { mostrarSesion(false, ''); return false; }
    const r = await api('/api/metricas?horas=1');
    if (r.ok) { mostrarSesion(true); return true; }
    guardarClave('');
    mostrarSesion(false, mensajeError(r.estado, r.datos, r.reintentarEn).titulo, 'error');
    return false;
  }

  function cerrarSesion(mensaje) {
    guardarClave('');
    ejemplos = {};
    sondeo += 1;                       // corta la espera de un resumen que estuviera en curso
    $('resultado').replaceChildren(h('p', { class: 'vacio' }, 'Evalúa una transacción para ver la decisión, sus razones y la versión del modelo.'));
    $('cola').replaceChildren(h('p', { class: 'vacio' }, 'Ingresa la clave para ver la cola.'));
    $('dashboard').replaceChildren(h('p', { class: 'vacio' }, 'Ingresa la clave para ver las métricas.'));
    mostrarSesion(false, mensaje || '', mensaje ? 'error' : '');
    $('clave').focus();
  }

  let ejemplos = {};
  async function cargarEjemplos() {
    const r = await api('/api/decision/ejemplos');
    ejemplos = r.ok ? r.datos : {};
  }

  $('form-clave').addEventListener('submit', async evento => {
    evento.preventDefault();
    const valor = $('clave').value.trim();
    if (!valor) { mostrarSesion(false, 'Escribe la clave de la API.', 'error'); return; }
    const boton = $('entrar');
    boton.disabled = true;
    boton.textContent = 'Comprobando…';
    guardarClave(valor);
    $('clave').value = '';
    const ok = await verificarClave();
    boton.disabled = false;
    boton.textContent = 'Entrar';
    if (ok) {
      await cargarEjemplos();
      cargarPanelActivo();
    }
  });
  $('ver-clave').addEventListener('click', () => {
    const visible = $('clave').type === 'password';
    $('clave').type = visible ? 'text' : 'password';
    $('ver-clave').textContent = visible ? 'Ocultar' : 'Mostrar';
    $('ver-clave').setAttribute('aria-pressed', String(visible));
  });
  $('salir').addEventListener('click', () => cerrarSesion());

  // ------------------------------------------------------------------------------------------------------------- pestañas
  function mostrarPestana(nombre) {
    for (const boton of document.querySelectorAll('.pestana')) boton.setAttribute('aria-selected', String(boton.dataset.panel === nombre));
    for (const panel of document.querySelectorAll('.panel')) panel.hidden = panel.id !== `panel-${nombre}`;
    if (location.hash !== `#${nombre}`) history.replaceState(null, '', `#${nombre}`);
    cargarPanelActivo();
  }
  const panelActivo = () => (document.querySelector('.pestana[aria-selected="true"]') || {}).dataset?.panel || 'evaluar';
  function cargarPanelActivo() {
    const p = panelActivo();
    if (p === 'revision') cargarRevision();
    if (p === 'dashboard') cargarDashboard();
  }
  for (const boton of document.querySelectorAll('.pestana')) boton.addEventListener('click', () => mostrarPestana(boton.dataset.panel));

  // ------------------------------------------------------------------------------------------------------ evaluar y resultado
  const formulario = $('formulario');
  const camposDelFormulario = () => Object.fromEntries(new FormData(formulario));

  function actualizarCurl() {
    $('curl').textContent = armarCurl(location.origin, cuerpoDeFormulario(camposDelFormulario()));
  }
  formulario.addEventListener('input', actualizarCurl);
  $('copiar-curl').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('curl').textContent); $('copiar-curl').textContent = 'Copiado ✔'; } catch { $('copiar-curl').textContent = 'No se pudo copiar'; }
    setTimeout(() => { $('copiar-curl').textContent = 'Copiar'; }, 2000);
  });

  function ponerEjemplo(tipo) {
    const n = ejemplos.normal || {};
    const hoy = new Date().toISOString().slice(0, 10);
    const datos = {
      normal: { usuario_id: n.usuario_id, dispositivo_id: n.dispositivo_id, comercio_id: n.comercio_id, monto: 12000, moneda: 'CLP', canal: 'app', pais_transaccion: 'CL', fecha_hora: '' },
      sospechosa: { usuario_id: n.usuario_id, dispositivo_id: (ejemplos.dispositivo_compartido || {}).dispositivo_id, comercio_id: n.comercio_id, monto: 180, moneda: 'USD', canal: 'web', pais_transaccion: 'NG', fecha_hora: `${hoy}T03:10` },
      lista_negra: { usuario_id: n.usuario_id, dispositivo_id: (ejemplos.lista_negra || {}).dispositivo_id, comercio_id: n.comercio_id, monto: 25000, moneda: 'CLP', canal: 'app', pais_transaccion: 'CL', fecha_hora: '' },
    }[tipo];
    for (const [campo, valor] of Object.entries(datos)) formulario.elements[campo].value = valor ?? '';
    actualizarCurl();
  }
  for (const boton of document.querySelectorAll('[data-ejemplo]')) {
    boton.addEventListener('click', async () => {
      if (!Object.keys(ejemplos).length && hayClave()) await cargarEjemplos();
      if (!Object.keys(ejemplos).length) { $('resultado').replaceChildren(h('div', { class: 'error-caja', role: 'alert' }, 'Para cargar un ejemplo ingresa antes la clave de la API.')); return; }
      ponerEjemplo(boton.dataset.ejemplo);
    });
  }

  function pintarResumen(contenedor, llm) {
    const i = infoResumen(llm);
    contenedor.className = `resumen ${i.clase}`;
    contenedor.replaceChildren(...sinNulos([
      h('h3', {}, i.clase === 'cargando' ? h('span', { class: 'giro', 'aria-hidden': 'true' }) : null, i.titulo),
      i.texto ? h('p', { class: 'texto' }, i.texto) : null,
      llm && llm.estado === 'listo' ? h('p', { class: 'nota' }, `Redactado por un LLM a partir de las razones del modelo${llm.latencia_ms ? ` (${numero(llm.latencia_ms)} ms)` : ''}. La decisión la tomó el modelo.`) : null]));
  }

  function pintarResultado(d) {
    const info = infoDecision(d.decision);
    const razones = (d.razones || []).map(r => {
      const peso = typeof r.aporte === 'number' ? Math.min(100, Math.round(Math.abs(r.aporte) * 200)) : null;
      return h('li', { class: 'razon' },
        r.efecto ? h('span', { class: `flecha ${r.efecto}`, 'aria-hidden': 'true' }, r.efecto === 'sube' ? '↑' : '↓') : null,
        r.condicion || r.regla || '',
        peso === null ? null : h('div', { class: 'barra-peso', 'aria-hidden': 'true' }, (() => { const s = h('span'); s.style.width = `${Math.max(peso, 4)}%`; return s; })()));
    });
    const dato = (etq, valor) => h('div', {}, h('dt', {}, etq), h('dd', {}, valor));
    const resumen = h('div', { id: 'resumen' });
    $('resultado').replaceChildren(...sinNulos([
      h('div', { class: `decision ${info.clase}` }, h('span', { 'aria-hidden': 'true' }, info.icono), h('span', {}, info.texto)),
      h('p', { class: `origen${d.origen === 'reserva' ? ' alerta' : ''}` }, ORIGENES[d.origen] || d.origen),
      h('dl', { class: 'meta' },
        dato('Probabilidad del modelo', d.score === null || d.score === undefined ? '—' : numero(d.score, 3)),
        dato('Versión del modelo', d.modelo_version || '— (no se consultó)'),
        dato('Umbrales', `revisar ${d.umbrales.revisar} · bloquear ${d.umbrales.bloquear}`),
        dato('Tiempo del dominio', `${numero(d.latencia_ms, 1)} ms`),
        dato('Transacción', d.transaccion_id),
        dato('Réplica de IA', d.replica_ia || '—')),
      d.score === null || d.score === undefined ? null : h('p', { class: 'aviso-score' }, 'La probabilidad sale de una muestra con mucho más fraude que el real: sirve para ordenar casos, no como una chance literal.'),
      h('h3', { class: 'subtitulo' }, 'Por qué (de mayor a menor peso)'),
      h('ul', { class: 'razones' }, razones.length ? razones : h('li', { class: 'razon' }, 'Sin razones destacadas.')),
      resumen]));
    pintarResumen(resumen, d.llm);
  }

  let sondeo = 0;                     // cada evaluacion nueva cancela la espera de la anterior
  async function esperarResumen(id) {
    const mio = ++sondeo;
    const inicio = Date.now();
    while (mio === sondeo && Date.now() - inicio < 90000) {
      await pausa(1500);
      if (mio !== sondeo) return;
      const r = await api(`/api/predicciones/${encodeURIComponent(id)}`);
      if (mio !== sondeo) return;
      const contenedor = $('resumen');
      if (r.ok && contenedor) {
        pintarResumen(contenedor, r.datos.llm);
        if (r.datos.llm.estado !== 'pendiente') return;
      } else if (r.estado === 429) {
        await pausa((Number(r.reintentarEn) || 2) * 1000);
      } else if (!r.ok) {
        return;
      }
    }
    if (mio === sondeo && $('resumen')) pintarResumen($('resumen'), { estado: 'degradado', motivo: 'el resumen tarda más de lo esperado; consúltalo de nuevo en la cola de revisión' });
  }

  formulario.addEventListener('submit', async evento => {
    evento.preventDefault();
    const boton = $('evaluar');
    if (!hayClave()) { $('resultado').replaceChildren(h('div', { class: 'error-caja', role: 'alert' }, 'Ingresa primero la clave de la API.')); return; }
    boton.disabled = true;
    boton.textContent = 'Evaluando…';
    sondeo += 1;
    $('resultado').replaceChildren(h('p', { class: 'vacio' }, h('span', { class: 'giro', 'aria-hidden': 'true' }), 'Evaluando…'));
    const r = await api('/api/predict', { metodo: 'POST', cuerpo: cuerpoDeFormulario(camposDelFormulario()) });
    boton.disabled = false;
    boton.textContent = 'Evaluar';
    if (!r.ok) { $('resultado').replaceChildren(cajaError(r)); return; }
    pintarResultado(r.datos);
    if (r.datos.llm.estado === 'pendiente') esperarResumen(r.datos.transaccion_id);
  });

  // --------------------------------------------------------------------------------------------------------- cola de revisión
  let filtro = 'todas';
  for (const chip of document.querySelectorAll('[data-filtro]')) {
    chip.addEventListener('click', () => {
      filtro = chip.dataset.filtro;
      for (const c of document.querySelectorAll('[data-filtro]')) c.classList.toggle('activo', c === chip);
      cargarRevision();
    });
  }
  $('actualizar-revision').addEventListener('click', () => cargarRevision());

  async function cargarRevision() {
    const cola = $('cola');
    if (!hayClave()) { cola.replaceChildren(h('p', { class: 'vacio' }, 'Ingresa la clave para ver la cola.')); return; }
    const r = await api(`/api/revision?limite=20&decision=${filtro}`);
    if (!r.ok) { cola.replaceChildren(cajaError(r)); return; }
    if (!r.datos.cola.length) { cola.replaceChildren(h('p', { class: 'vacio' }, 'No hay transacciones marcadas todavía.')); return; }
    cola.replaceChildren(...r.datos.cola.map(f => {
      const info = infoDecision(f.decision);
      const razones = (f.razones || []).slice(0, 2).map(x => x.condicion || x.regla || '').filter(Boolean);
      const resumen = h('div', {});
      pintarResumen(resumen, f.llm);
      return h('article', { class: 'item' },
        h('div', { class: 'item-cab' }, h('span', { class: `badge ${info.clase}` }, info.texto), h('span', { class: 'prioridad' }, `prioridad ${numero(f.prioridad, 0)}`)),
        h('p', { class: 'detalle' }, `${f.transaccion_id} · USD ${numero(f.monto_usd, 0)} · ${f.canal} · ${new Date(f.creado_en).toLocaleString('es-CL')}`),
        razones.length ? h('p', { class: 'detalle' }, razones.join(' · ')) : null,
        resumen);
    }));
  }

  // -------------------------------------------------------------------------------------------------------------- dashboard
  $('ventana').addEventListener('change', () => cargarDashboard());

  function barras(titulo, mapa, clases = {}) {
    const total = Object.values(mapa).reduce((a, b) => a + b, 0);
    const filas = Object.entries(mapa).map(([clave, n]) => {
      const barra = h('span', { class: clases[clave] || '' });
      barra.style.width = `${porcentaje(n, total)}%`;
      return h('div', { class: 'barra-fila' }, h('span', {}, clave.replace('_', ' ')), h('div', { class: 'pista' }, barra), h('span', { class: 'n' }, numero(n)));
    });
    // Un solo elemento (no una lista): replaceChildren no despliega listas y mostraria "[object HTMLDivElement]".
    return h('section', {}, h('h3', { class: 'grupo' }, titulo),
      h('div', { class: 'barras' }, filas.length ? filas : h('p', { class: 'vacio' }, 'Sin datos en esta ventana.')));
  }

  async function cargarDashboard() {
    const caja = $('dashboard');
    if (!hayClave()) { caja.replaceChildren(h('p', { class: 'vacio' }, 'Ingresa la clave para ver las métricas.')); return; }
    const r = await api(`/api/metricas?horas=${$('ventana').value}`);
    if (!r.ok) { caja.replaceChildren(cajaError(r)); return; }
    const m = r.datos;
    const kpi = (valor, etq, clase) => h('div', { class: `kpi ${clase || ''}` }, h('div', { class: 'valor' }, valor), h('div', { class: 'etq' }, etq));
    const maxTasa = Math.max(1, ...m.bloqueo_por_dia.map(d => d.tasa_bloqueo_pct));
    const dias = m.bloqueo_por_dia.map(d => {
      const col = h('span', { title: `${d.bloqueadas} bloqueadas de ${d.evaluadas}` });
      col.style.height = `${Math.max(2, Math.round((90 * d.tasa_bloqueo_pct) / maxTasa))}px`;
      return h('div', { class: 'dia' }, h('div', { class: 'col' }, col), h('b', {}, `${numero(d.tasa_bloqueo_pct, 1)}%`), diaCorto(d.dia));
    });
    caja.replaceChildren(
      h('div', { class: 'kpis' },
        kpi(numero(m.evaluadas), 'decisiones evaluadas'),
        kpi(`${numero(m.pct_reserva, 1)}%`, 'salieron por la reserva (alerta sobre 1%)', m.pct_reserva > 1 ? 'aviso' : ''),
        kpi(m.latencia_ms.p95 === null ? '—' : `${numero(m.latencia_ms.p95, 0)} ms`, `latencia del dominio: p50 ${numero(m.latencia_ms.p50, 0)} · p99 ${numero(m.latencia_ms.p99, 0)}`),
        kpi(numero(m.en_cola_de_revision), 'en la cola de revisión')),
      barras('Decisiones', m.por_decision, { aprobar: 'ok', revisar: 'aviso', bloquear: 'peligro' }),
      barras('Quién decidió', m.por_origen, { reserva: 'aviso', lista_negra: 'peligro' }),
      barras('Resúmenes del LLM', m.resumenes_llm, { listo: 'ok', degradado: 'aviso' }),
      h('h3', { class: 'grupo' }, 'Tasa de bloqueo por día (RF12, últimos 14 días)'),
      dias.length ? h('div', { class: 'dias' }, dias) : h('p', { class: 'vacio' }, 'Sin datos todavía.'));
  }

  // ------------------------------------------------------------------------------------------------------------- arranque
  actualizarCurl();
  actualizarSalud();
  setInterval(() => { if (!document.hidden) actualizarSalud(); }, 30000);
  setInterval(() => { if (!document.hidden && hayClave()) cargarPanelActivo(); }, 20000);
  const inicial = location.hash.replace('#', '');
  if (['evaluar', 'revision', 'dashboard'].includes(inicial)) mostrarPestana(inicial);
  if (hayClave()) {
    $('estado-clave').textContent = 'Comprobando la clave…';
    verificarClave().then(ok => { if (ok) { cargarEjemplos(); cargarPanelActivo(); } });
  }
}
