import Swal from 'sweetalert2';
import { formatearCuit } from '../../core/cuit.pipe';
import { Advertencia, CeldaVista, ComprobanteLetraDistinta, FilaConError } from '../../core/models';

/**
 * Ventana de hallazgos al terminar un control: qué encontró la auditoría (y qué
 * pasos fallaron), qué hacer y las filas tal como están en el Excel, con el
 * error remarcado. Solo muestra: no modifica nada.
 *
 * - Las píldoras del encabezado («X corregir», «Y info») filtran las tarjetas.
 * - Las acciones que llevan a algún lado son botones a la derecha; las demás son
 *   etiquetas de estado debajo del título.
 * - Las tarjetas INFO arrancan plegadas («Ver detalle ▾»).
 * - Un solo scroll (el de la lista) y «Entendido» siempre a la vista.
 */

const ORDEN_GRAVEDAD: Record<Advertencia['gravedad'], number> = { grave: 0, corregir: 1, info: 2 };

const ETIQUETA_GRAVEDAD: Record<Advertencia['gravedad'], string> = {
  grave: 'Grave',
  corregir: 'Corregir',
  info: 'Info',
};

/** Columnas comunes a ARCA y Xubio (cada planilla las llama distinto). */
const COLUMNAS = ['Fecha', 'Tipo', 'Comprobante', 'CUIT', 'Razón social', 'Total'] as const;
type Columna = (typeof COLUMNAS)[number];
const ALIAS: Record<string, Columna> = {
  Fecha: 'Fecha',
  Tipo: 'Tipo',
  Comprobante: 'Comprobante',
  CUIT: 'CUIT',
  'Nro. Doc.': 'CUIT',
  Denominación: 'Razón social',
  Proveedor: 'Razón social',
  Cliente: 'Razón social',
  'Imp. Total': 'Total',
  Total: 'Total',
};

function esc(texto: unknown): string {
  return String(texto ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function texto(v: CeldaVista['valor'] | undefined): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'number') return v.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return String(v);
}

/** La CUIT siempre como XX-XXXXXXXX-X (ARCA la manda sin guiones y Xubio con). */
function cuit(v: CeldaVista['valor'] | undefined): string {
  if (v === null || v === undefined) return '';
  return formatearCuit(typeof v === 'number' ? String(Math.trunc(v)) : String(v));
}

function normalizar(fila: CeldaVista[]): Partial<Record<Columna, CeldaVista['valor']>> {
  const salida: Partial<Record<Columna, CeldaVista['valor']>> = {};
  for (const c of fila) {
    const col = ALIAS[c.columna];
    if (col && salida[col] === undefined) salida[col] = c.valor;
  }
  return salida;
}

interface FilaVista {
  origen: 'ARCA' | 'Xubio';
  datos: Partial<Record<Columna, CeldaVista['valor']>>;
  /** HTML ya armado para la celda Comprobante (con la letra marcada). */
  comprobante?: string;
  marca?: 'error' | 'ok';
}

/** Una grilla estilo Excel con las filas de ARCA y Xubio juntas. */
function grilla(filas: FilaVista[]): string {
  if (!filas.length) return '';
  const cab = ['', ...COLUMNAS].map((c) => `<th${c === 'Total' ? ' class="num"' : ''}>${esc(c)}</th>`).join('');
  const cuerpo = filas
    .map((f) => {
      const celdas = COLUMNAS.map((col) => {
        if (col === 'Comprobante') {
          const clase = f.marca ? ` ${f.marca}` : '';
          return `<td class="comp${clase}">${f.comprobante ?? esc(texto(f.datos[col]))}</td>`;
        }
        if (col === 'CUIT') return `<td class="cuit">${esc(cuit(f.datos[col]))}</td>`;
        if (col === 'Total') return `<td class="num">${esc(texto(f.datos[col]))}</td>`;
        return `<td>${esc(texto(f.datos[col]))}</td>`;
      }).join('');
      return `<tr><td class="origen origen-${f.origen.toLowerCase()}">${f.origen}</td>${celdas}</tr>`;
    })
    .join('');
  return `<div class="ve-grilla"><table><thead><tr>${cab}</tr></thead><tbody>${cuerpo}</tbody></table></div>`;
}

function conLetra(comprobante: string, clase: 'error' | 'ok'): string {
  const [letra, ...resto] = comprobante.split('-');
  return `<span class="letra letra-${clase}">${esc(letra)}</span>-${esc(resto.join('-'))}`;
}

function filasDe(e: Advertencia): FilaVista[] {
  if (e.tipo === 'letra_distinta') {
    return (e.comprobantes as ComprobanteLetraDistinta[]).flatMap((c) => [
      { origen: 'ARCA', datos: normalizar(c.fila_arca), comprobante: conLetra(c.comprobante_arca, 'ok'), marca: 'ok' },
      { origen: 'Xubio', datos: normalizar(c.fila_xubio), comprobante: conLetra(c.comprobante_xubio, 'error'), marca: 'error' },
    ]);
  }
  return (e.comprobantes as FilaConError[]).map((f) => ({
    origen: f.origen === 'arca' ? 'ARCA' : 'Xubio',
    datos: normalizar(f.fila),
    marca: e.gravedad === 'info' ? undefined : 'error',
  }));
}

/** La orden de cada letra distinta: «Soto Stockmayer: A → C». */
function ordenesLetra(e: Advertencia): string {
  if (e.tipo !== 'letra_distinta') return '';
  return (e.comprobantes as ComprobanteLetraDistinta[])
    .map(
      (c) =>
        `<li>${esc(c.proveedor)} · ${esc(c.comprobante_xubio)}: <span class="letra letra-error">${esc(c.letra_xubio)}</span> → <span class="letra letra-ok">${esc(c.letra_arca)}</span></li>`,
    )
    .join('');
}

// --- Acciones: botones (llevan a algún lado) vs. etiquetas (estado) ----------------

const XUBIO = 'https://xubio.com';

/** Lo que tarda Xubio en cambiar de empresa antes de ir al Libro IVA. */
const ESPERA_CAMBIO_EMPRESA_MS = 5000;

/** Solo direcciones https de xubio.com (el link de la empresa viene del bot). */
function esUrlDeXubio(url: string | undefined): url is string {
  try {
    const u = new URL(url ?? '');
    return u.protocol === 'https:' && (u.hostname === 'xubio.com' || u.hostname.endsWith('.xubio.com'));
  } catch {
    return false;
  }
}

/** El primer comprobante de la tarjeta tal como está en Xubio (para pegarlo en el buscador). */
function numeroParaBuscar(e: Advertencia): string {
  const primero = e.comprobantes[0];
  if (!primero) return '';
  if (e.tipo === 'letra_distinta') return (primero as ComprobanteLetraDistinta).comprobante_xubio ?? '';
  return texto(normalizar((primero as FilaConError).fila).Comprobante);
}

/**
 * «Corregir en Xubio»: abre Xubio en otra pestaña ya en la empresa del cliente y,
 * cuando cambió de empresa, lo lleva al Libro IVA de esa operación. Además copia
 * el número del comprobante para pegarlo en el buscador.
 *
 * Xubio no da un link al comprobante (el Excel no trae su código interno): por
 * eso se llega a la empresa y al listado, no a la pantalla del comprobante.
 */
function abrirEnXubio(boton: HTMLElement): void {
  const empresa = boton.dataset['empresa'];
  const listado = `${XUBIO}/NXV/impuestos/libro-iva-${boton.dataset['operacion'] === 'Ventas' ? 'ventas' : 'compras'}`;
  const numero = boton.dataset['numero'] ?? '';
  const aviso = boton.closest('.ve-error')?.querySelector<HTMLElement>('.ve-copiado');
  const avisar = (mensaje: string) => {
    if (aviso) aviso.textContent = mensaje;
  };

  if (numero && navigator.clipboard) {
    navigator.clipboard.writeText(numero).then(
      () => avisar(`Número copiado: ${numero}. Pegalo en el buscador de Xubio.`),
      () => avisar(`Buscá en Xubio el comprobante ${numero}.`),
    );
  } else if (numero) {
    avisar(`Buscá en Xubio el comprobante ${numero}.`);
  }

  // Se abre en blanco para cortarle a Xubio el acceso a esta página (opener) y
  // después se navega: primero a la empresa y, ya cambiada, al Libro IVA.
  const pestana = window.open('about:blank', '_blank');
  if (!pestana) {
    window.open(esUrlDeXubio(empresa) ? empresa : listado, '_blank', 'noopener');
    return;
  }
  pestana.opener = null;
  if (esUrlDeXubio(empresa)) {
    pestana.location.href = empresa;
    window.setTimeout(() => {
      try {
        pestana.location.href = listado;
      } catch {
        // La pestaña se cerró: no hay nada que hacer.
      }
    }, ESPERA_CAMBIO_EMPRESA_MS);
  } else {
    pestana.location.href = listado;
  }
}

function destinoDe(accion: string): { href: string; nuevaPestana: boolean } | null {
  const a = accion.toLowerCase();
  if (a.includes('clave de arca') || a.includes('cuit del cliente')) return { href: '/clientes', nuevaPestana: false };
  if (a.includes('volver a correr')) return { href: '/control', nuevaPestana: false };
  return null;
}

function botonesYEtiquetas(e: Advertencia, enlaces: Readonly<Record<string, string>>): { botones: string; etiquetas: string } {
  const botones: string[] = [];
  const etiquetas: string[] = [];
  for (const accion of e.acciones) {
    const destino = destinoDe(accion);
    if (accion.toLowerCase().includes('xubio')) {
      const empresa = esUrlDeXubio(enlaces[e.cliente]) ? enlaces[e.cliente] : '';
      const ayuda = empresa
        ? 'Abre Xubio en la empresa del cliente y el Libro IVA, y copia el número del comprobante'
        : 'Abre el Libro IVA de Xubio y copia el número del comprobante';
      botones.push(
        `<button type="button" class="ve-boton" data-xubio data-empresa="${esc(empresa)}" data-operacion="${esc(e.operacion ?? '')}" data-numero="${esc(numeroParaBuscar(e))}" title="${ayuda}">→ ${esc(accion)}</button>`,
      );
    } else if (destino) {
      const pestana = destino.nuevaPestana ? ' target="_blank" rel="noopener"' : '';
      botones.push(`<a class="ve-boton" href="${esc(destino.href)}"${pestana}>→ ${esc(accion)}</a>`);
    } else {
      const nada = accion === 'Sin acción';
      etiquetas.push(`<span class="ve-etiqueta">${nada ? '✓ ' : ''}${esc(accion)}</span>`);
    }
  }
  return { botones: botones.join(''), etiquetas: etiquetas.join('') };
}

/** Los filtros del encabezado: «corregir» junta grave y corregir; «info» aparte. */
type Grupo = 'corregir' | 'info';

function grupoDe(g: Advertencia['gravedad']): Grupo {
  return g === 'info' ? 'info' : 'corregir';
}

function tarjeta(e: Advertencia, enlaces: Readonly<Record<string, string>>): string {
  const meta = [e.operacion, e.total ? `${e.total} comprobante${e.total === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ');
  const mas =
    e.total > e.comprobantes.length
      ? `<p class="ve-mas">${e.comprobantes.length} de ${e.total}${e.hoja ? ` · el resto en la hoja «${esc(e.hoja)}»` : ''}</p>`
      : '';
  const letras = ordenesLetra(e);
  const { botones, etiquetas } = botonesYEtiquetas(e, enlaces);
  // Las INFO arrancan plegadas: una fila compacta con «Ver detalle ▾».
  return `
    <details class="ve-error ve-${e.gravedad}" data-grupo="${grupoDe(e.gravedad)}" ${e.gravedad === 'info' ? '' : 'open'}>
      <summary>
        <span class="ve-insignia">${ETIQUETA_GRAVEDAD[e.gravedad]}</span>
        <span class="ve-cab">
          <span class="ve-titulo">${esc(e.titulo)}</span>
          ${etiquetas ? `<span class="ve-etiquetas">${etiquetas}</span>` : ''}
        </span>
        <span class="ve-derecha">
          <span class="ve-meta">${esc(meta)}</span>
          ${botones}
          <span class="ve-plegar" aria-hidden="true"></span>
        </span>
      </summary>
      <div class="ve-cuerpo">
        <p class="ve-copiado" role="status" aria-live="polite"></p>
        ${e.detalle ? `<p class="ve-detalle">${esc(e.detalle)}</p>` : ''}
        ${letras ? `<ul class="ve-ordenes">${letras}</ul>` : ''}
        ${grilla(filasDe(e))}${mas}
      </div>
    </details>`;
}

const ESTILOS = `
  <style>
    /* Pie fijo: scrollea la lista y «Entendido» queda siempre a la vista. */
    /* El popup es una grilla: ícono, título, lista y pie. La lista toma el alto que
       queda (minmax(0, 1fr)) y scrollea; el pie nunca se va de la pantalla. */
    .ve-popup { max-height: 94vh; grid-template-rows: auto auto minmax(0, 1fr) auto; }
    .ve-popup .swal2-icon { margin: 16px auto 0; width: 3.4em; height: 3.4em; }
    .ve-popup .swal2-icon .swal2-icon-content { font-size: 2.4em; }
    .ve-popup .swal2-title { padding-top: 8px; }
    .ve-popup .swal2-html-container { margin: 8px 0 0; padding: 0 18px; min-height: 0; max-height: none; overflow-y: auto; overscroll-behavior: contain; }
    .ve-popup .swal2-actions { margin: 0; padding: 12px 0 14px; width: 100%; border-top: 1px solid #e2e8f0; background: #fff; }
    .ve { text-align: left; color: #1f2a37; font-size: 13.5px; }
    .ve-intro { margin: 0 0 8px; text-align: center; color: #475569; }
    .ve-resumen { display: flex; justify-content: center; flex-wrap: wrap; gap: 6px; margin-bottom: 12px; }
    .ve-chip { border-radius: 999px; padding: 3px 12px; font-family: inherit; font-size: 12px; font-weight: 700; white-space: nowrap; cursor: pointer; border: 1.5px solid transparent; transition: box-shadow .15s, opacity .15s; }
    .ve-chip:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
    .ve-chip[aria-pressed="true"] { border-color: currentColor; box-shadow: 0 0 0 3px rgba(37, 99, 235, .12); }
    .ve-resumen.filtrando .ve-chip[aria-pressed="false"] { opacity: .5; }
    .ve-lista { padding: 10px; background: #f6f8fc; border-radius: 8px; }
    .ve-grupo[hidden], .ve-error[hidden] { display: none; }
    .ve-cliente { margin: 4px 0 8px; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #64748b; }
    .ve-error { background: #fff; border: 1px solid #e1e7f2; border-radius: 8px; margin-bottom: 10px; overflow: hidden; }
    .ve-error summary { cursor: pointer; display: flex; align-items: flex-start; gap: 10px; padding: 10px 14px; list-style: none; }
    .ve-error summary::-webkit-details-marker { display: none; }
    .ve-insignia { flex: none; margin-top: 1px; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; letter-spacing: .03em; }
    .ve-cab { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
    .ve-titulo { font-weight: 700; }
    .ve-etiquetas { display: flex; flex-wrap: wrap; gap: 6px; }
    .ve-etiqueta { display: inline-flex; align-items: center; font-size: 11.5px; font-weight: 600; padding: 1px 9px; border-radius: 999px; background: #eef2f7; color: #475569; border: 1px solid #dfe5ee; }
    .ve-derecha { flex: none; display: flex; align-items: center; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
    .ve-meta { font-size: 12px; color: #64748b; white-space: nowrap; }
    .ve-copiado { margin: 0 0 6px; font-size: 12.5px; font-weight: 700; color: #15803d; }
    .ve-copiado:empty { display: none; }
    .ve-boton { border: 0; cursor: pointer; font-family: inherit; display: inline-flex; align-items: center; font-size: 12px; font-weight: 700; padding: 5px 12px; border-radius: 8px; background: #2563eb; color: #fff !important; text-decoration: none; box-shadow: 0 1px 2px rgba(15, 23, 42, .15); white-space: nowrap; }
    .ve-boton:hover { background: #1d4ed8; }
    .ve-boton:focus-visible { outline: 2px solid #1d4ed8; outline-offset: 2px; }
    .ve-plegar { font-size: 12px; font-weight: 700; color: #2563eb; white-space: nowrap; }
    .ve-plegar::after { content: 'Ver detalle ▾'; }
    details[open] > summary .ve-plegar::after { content: 'Ocultar ▴'; }
    .ve-cuerpo { padding: 0 14px 12px; }
    .ve-ordenes { margin: -2px 0 8px; padding: 0; list-style: none; }
    .ve-ordenes li { margin: 2px 0; font-family: Consolas, 'Cascadia Mono', monospace; font-size: 12.5px; }
    .ve-detalle { margin: 0 0 8px; color: #64748b; font-size: 12.5px; }
    .ve-mas { margin: 6px 0 0; font-size: 12px; color: #64748b; }
    .ve-grave .ve-insignia, .ve-chip-grave { background: #fde2e2; color: #9b2c2c; }
    .ve-corregir .ve-insignia, .ve-chip-corregir { background: #fdf0d9; color: #7a4f0f; }
    .ve-info .ve-insignia, .ve-chip-info { background: #e8eef9; color: #1d4ed8; }
    .ve-grave { border-left: 3px solid #c53030; }
    .ve-corregir { border-left: 3px solid #b7791f; }
    .ve-info { border-left: 3px solid #93a8d6; }
    /* Sin alto máximo: un solo scroll (el de la lista), nunca dos anidados. */
    .ve-grilla { border: 1px solid #d4d4d4; border-radius: 4px; overflow-x: auto; }
    .ve-grilla table { border-collapse: collapse; width: 100%; font: 12px Calibri, 'Segoe UI', Arial, sans-serif; white-space: nowrap; }
    .ve-grilla th { background: #f3f3f3; color: #444; font-weight: 600; text-align: left; padding: 4px 8px; border-bottom: 1px solid #d4d4d4; border-right: 1px solid #e4e4e4; }
    .ve-grilla th.num { text-align: right; }
    .ve-grilla td { padding: 4px 8px; border-bottom: 1px solid #ececec; border-right: 1px solid #f0f0f0; }
    .ve-grilla td.num { text-align: right; font-variant-numeric: tabular-nums; }
    .ve-grilla td.cuit { font-family: Consolas, 'Cascadia Mono', monospace; font-variant-numeric: tabular-nums; }
    .ve-grilla td.origen { font-weight: 700; font-size: 11px; color: #fff; text-align: center; width: 1%; }
    .ve-grilla td.origen-arca { background: #1b2f57; }
    .ve-grilla td.origen-xubio { background: #217346; }
    .ve-grilla td.comp { font-family: Consolas, 'Cascadia Mono', monospace; }
    .ve-grilla td.comp.error { background: #fdeaea; box-shadow: inset 0 0 0 1.5px #c53030; }
    .ve-grilla td.comp.ok { background: #e7f5ec; box-shadow: inset 0 0 0 1.5px #15803d; }
    .letra { display: inline-block; min-width: 16px; text-align: center; border-radius: 3px; color: #fff; font-weight: 700; padding: 0 3px; }
    .letra-error { background: #c53030; }
    .letra-ok { background: #15803d; }
    @media (max-width: 640px) {
      .ve-error summary { flex-wrap: wrap; }
      .ve-derecha { width: 100%; justify-content: flex-start; }
    }
  </style>`;

/** Cuántos errores hay por gravedad. */
export function contarErrores(errores: readonly Advertencia[]): Record<Advertencia['gravedad'], number> {
  const n = { grave: 0, corregir: 0, info: 0 };
  for (const e of errores) n[e.gravedad] = (n[e.gravedad] ?? 0) + 1;
  return n;
}

/** Las píldoras filtran las tarjetas: una activa muestra solo esas; otro click, todas. */
function activarBotonesXubio(raiz: HTMLElement): void {
  for (const boton of raiz.querySelectorAll<HTMLElement>('[data-xubio]')) {
    boton.addEventListener('click', (ev) => {
      // El botón está dentro del <summary>: que no pliegue la tarjeta.
      ev.preventDefault();
      ev.stopPropagation();
      abrirEnXubio(boton);
    });
  }
}

function activarFiltros(raiz: HTMLElement): void {
  const resumen = raiz.querySelector<HTMLElement>('.ve-resumen');
  const chips = [...raiz.querySelectorAll<HTMLButtonElement>('.ve-chip[data-filtro]')];
  let actual: string | null = null;

  const aplicar = () => {
    resumen?.classList.toggle('filtrando', !!actual);
    for (const c of chips) c.setAttribute('aria-pressed', String(c.dataset['filtro'] === actual));
    for (const grupo of raiz.querySelectorAll<HTMLElement>('.ve-grupo')) {
      let visibles = 0;
      for (const t of grupo.querySelectorAll<HTMLElement>('.ve-error')) {
        const ver = !actual || t.dataset['grupo'] === actual;
        t.hidden = !ver;
        if (ver) visibles++;
      }
      grupo.hidden = visibles === 0;
    }
  };

  for (const c of chips) {
    c.addEventListener('click', () => {
      actual = actual === c.dataset['filtro'] ? null : (c.dataset['filtro'] ?? null);
      aplicar();
    });
  }
  aplicar();
}

/** Abre la ventana de hallazgos. Devuelve la promesa de SweetAlert2 (se resuelve al cerrarla). */
export function mostrarVentanaDeErrores(errores: readonly Advertencia[], enlacesXubio: Readonly<Record<string, string>> = {}) {
  const n = contarErrores(errores);
  const aCorregir = n.grave + n.corregir;
  const total = errores.length;
  const ordenados = [...errores].sort((a, b) => ORDEN_GRAVEDAD[a.gravedad] - ORDEN_GRAVEDAD[b.gravedad]);
  const porCliente = new Map<string, Advertencia[]>();
  for (const e of ordenados) porCliente.set(e.cliente, [...(porCliente.get(e.cliente) ?? []), e]);

  const sub = n.grave
    ? 'El control terminó con errores graves: el resultado no es confiable.'
    : aCorregir
      ? 'El control se hizo correctamente. Hay datos para corregir.'
      : 'El control se hizo correctamente.';
  const chips = [
    aCorregir
      ? `<button type="button" class="ve-chip ve-chip-${n.grave ? 'grave' : 'corregir'}" data-filtro="corregir" aria-pressed="false">${aCorregir} corregir</button>`
      : '',
    n.info ? `<button type="button" class="ve-chip ve-chip-info" data-filtro="info" aria-pressed="false">${n.info} info</button>` : '',
  ].join('');
  const lista = [...porCliente.entries()]
    .map(([cliente, items]) => `<section class="ve-grupo"><p class="ve-cliente">${esc(cliente)}</p>${items.map((e) => tarjeta(e, enlacesXubio)).join('')}</section>`)
    .join('');

  return Swal.fire({
    icon: n.grave ? 'error' : aCorregir ? 'warning' : 'info',
    title: total ? `Hallazgos del control (${total})` : 'Control sin hallazgos',
    html: `${ESTILOS}
      <div class="ve">
        <p class="ve-intro">${sub}</p>
        ${chips ? `<div class="ve-resumen" role="group" aria-label="Filtrar hallazgos">${chips}</div>` : ''}
        <div class="ve-lista">${lista}</div>
      </div>`,
    width: 'min(980px, 96vw)',
    customClass: { popup: 've-popup' },
    confirmButtonText: 'Entendido',
    confirmButtonColor: '#2563eb',
    focusConfirm: true,
    didOpen: (popup) => {
      activarFiltros(popup);
      activarBotonesXubio(popup);
    },
    // Sin animación: si la pestaña está atrás (mirando el Chrome del bot), el
    // navegador congela las animaciones y la ventana quedaba transparente.
    showClass: { popup: '', backdrop: 'swal2-backdrop-show', icon: '' },
    hideClass: { popup: '', backdrop: '', icon: '' },
  });
}
