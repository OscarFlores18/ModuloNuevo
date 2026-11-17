import Swal from 'sweetalert2';
import { formatearCuit } from '../../core/cuit.pipe';
import { ResumenOperacion, Advertencia, CeldaVista, ComprobanteLetraDistinta, FilaConError } from '../../core/models';

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
  /** Etiqueta al lado del comprobante (ej. «Normal: extracto o tique»). */
  nota?: string;
}

/** Solo el exterior lleva etiqueta: en tiques y extractos ya lo dice la columna Tipo. */
const NOTA_MOTIVO: Partial<Record<NonNullable<FilaConError['motivo']>, string>> = {
  exterior: 'Normal · del exterior',
  anulada: 'Normal · anulada con nota de crédito',
  liquidacion: 'Normal · está en el extracto del banco',
};

/** Una grilla estilo Excel con las filas de ARCA y Xubio juntas. */
function grilla(filas: FilaVista[]): string {
  if (!filas.length) return '';
  const cab = ['', ...COLUMNAS].map((c) => `<th${c === 'Total' ? ' class="num"' : ''}>${esc(c)}</th>`).join('');
  const cuerpo = filas
    .map((f) => {
      const celdas = COLUMNAS.map((col) => {
        if (col === 'Comprobante') {
          const clase = f.marca ? ` ${f.marca}` : '';
          const nota = f.nota ? ` <span class="ve-nota">${esc(f.nota)}</span>` : '';
          return `<td class="comp${clase}">${f.comprobante ?? esc(texto(f.datos[col]))}${nota}</td>`;
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
    marca: e.gravedad === 'info' || f.motivo ? undefined : 'error',
    nota: f.motivo ? NOTA_MOTIVO[f.motivo] : undefined,
  }));
}

/** La orden de cada letra distinta: «Soto Stockmayer: A → C». */
function ordenesLetra(e: Advertencia): string {
  if (e.tipo !== 'letra_distinta') return '';
  return (e.comprobantes as ComprobanteLetraDistinta[])
    .map(
      (c) =>
        `<li><span class="ve-orden-quien"><strong>${esc(c.proveedor)}</strong> <span class="ve-orden-comp">${esc(c.comprobante_xubio)}</span></span><span class="ve-orden-cambio">Cambiar la letra <span class="letra letra-error">${esc(c.letra_xubio)}</span> <span class="ve-flecha">→</span> <span class="letra letra-ok">${esc(c.letra_arca)}</span></span></li>`,
    )
    .join('');
}

// --- Acciones: botones (llevan a algún lado) vs. etiquetas (estado) ----------------

function destinoDe(accion: string): { href: string; nuevaPestana: boolean } | null {
  const a = accion.toLowerCase();
  if (a.includes('clave de arca') || a.includes('cuit del cliente')) return { href: '/clientes', nuevaPestana: false };
  if (a.includes('volver a correr')) return { href: '/control', nuevaPestana: false };
  return null;
}

/** Lo que la ventana necesita saber además de los hallazgos (para armar el mail). */
export interface ContextoVentana {
  /** Mail de cada cliente (por nombre), cargado en Clientes. */
  emails: Record<string, string>;
  /** «01/11/2026 al 17/11/2026». */
  periodo: string;
}

const MAX_LINEAS_MAIL = 25;

/**
 * «Consultar al cliente»: abre el compose web de Gmail con el mail del cliente,
 * el asunto y la lista de comprobantes a revisar (sin los normales: tiques,
 * anuladas...).
 *
 * Se usa el compose de Gmail y no `mailto:` porque Windows resuelve `mailto:`
 * con la aplicación de correo predeterminada del sistema (en esta PC, Outlook),
 * y Gmail en el navegador nunca llegaba a abrirse.
 */
const COMPOSE_GMAIL = 'https://mail.google.com/mail/';

function mailConsulta(e: Advertencia, ctx: ContextoVentana): string {
  const filas = (e.comprobantes as FilaConError[]).filter((f) => 'fila' in f && !f.motivo);
  const lineas = filas.slice(0, MAX_LINEAS_MAIL).map((f) => {
    const d = normalizar(f.fila);
    const total = typeof d.Total === 'number' ? ` · $ ${texto(d.Total)}` : '';
    return `- ${texto(d.Fecha)} · ${texto(d.Tipo)} ${texto(d.Comprobante)} · ${texto(d['Razón social'])} (CUIT ${cuit(d.CUIT)})${total}`;
  });
  const resto = filas.length > MAX_LINEAS_MAIL ? `\n…y ${filas.length - MAX_LINEAS_MAIL} más.` : '';
  const operacion = (e.operacion ?? '').toLowerCase();
  const que =
    e.tipo === 'faltante_en_xubio'
      ? 'figuran en ARCA y no los tenemos registrados'
      : 'los tenemos registrados y no figuran en ARCA';
  const asunto = `Consulta de comprobantes de ${operacion} · ${ctx.periodo} · ${e.cliente}`;
  const cuerpo = [
    'Hola,',
    '',
    `Al controlar ${operacion} del período ${ctx.periodo} encontramos ${filas.length === 1 ? 'este comprobante que' : 'estos comprobantes que'} ${que}:`,
    '',
    lineas.join('\n') + resto,
    '',
    '¿Nos podés confirmar si corresponden? Si es así, por favor envianos el comprobante.',
    '',
    'Saludos.',
  ].join('\n');
  const para = (ctx.emails[e.cliente] ?? '').split(',').map((m) => m.trim()).filter(Boolean).join(',');
  const partes = [
    'view=cm',
    'fs=1',
    `su=${encodeURIComponent(asunto)}`,
    `body=${encodeURIComponent(cuerpo)}`,
  ];
  // Las comas y las @ del destinatario se dejan legibles: Gmail separa varios
  // mails con coma y los reconoce igual estando sin escapar.
  if (para) {
    partes.splice(2, 0, `to=${encodeURIComponent(para).replace(/%2C/g, ',').replace(/%40/g, '@')}`);
  }
  return `${COMPOSE_GMAIL}?${partes.join('&')}`;
}

function botonesYEtiquetas(e: Advertencia, ctx: ContextoVentana): { botones: string; etiquetas: string } {
  const botones: string[] = [];
  const etiquetas: string[] = [];
  for (const accion of e.acciones) {
    // «Corregir en Xubio» ya no se ofrece: lo que hay que cambiar está en el instructivo.
    if (accion.toLowerCase().includes('xubio')) continue;
    if (accion === 'Consultar al cliente') {
      const sinMail = !ctx.emails[e.cliente];
      const ayuda = sinMail
        ? 'El cliente no tiene email cargado: se abre Gmail sin destinatario (cargalo en Clientes).'
        : `Abre Gmail con un mail a ${ctx.emails[e.cliente]} y los comprobantes a revisar.`;
      botones.push(`<a class="ve-boton" href="${esc(mailConsulta(e, ctx))}" target="_blank" rel="noopener" title="${esc(ayuda)}">✉ Consultar al cliente</a>`);
      continue;
    }
    const destino = destinoDe(accion);
    if (destino) {
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

function tarjeta(e: Advertencia, ctx: ContextoVentana): string {
  const meta = [e.operacion, e.total ? `${e.total} comprobante${e.total === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ');
  const mas =
    e.total > e.comprobantes.length
      ? `<p class="ve-mas">${e.comprobantes.length} de ${e.total}${e.hoja ? ` · el resto en la hoja «${esc(e.hoja)}»` : ''}</p>`
      : '';
  const letras = ordenesLetra(e);
  const { botones, etiquetas } = botonesYEtiquetas(e, ctx);
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
        ${e.detalle ? `<p class="ve-detalle">${esc(e.detalle)}</p>` : ''}
        ${letras ? `<div class="ve-instructivo"><p class="ve-instructivo-titulo">Qué corregir en Xubio</p><ul class="ve-ordenes">${letras}</ul></div>` : ''}
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
    .ve-res { list-style: none; margin: 0 0 12px; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .ve-res li { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-radius: 8px; font-size: 14px; }
    .ve-res-ok { background: #e7f5ec; color: #14532d; }
    .ve-res-mal { background: #fdf0d9; color: #7a4f0f; }
    .ve-res-icono { flex: none; width: 22px; height: 22px; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; font-weight: 800; color: #fff; }
    .ve-res-ok .ve-res-icono { background: #15803d; }
    .ve-res-mal .ve-res-icono { background: #b7791f; }
    .ve-res-cliente { font-weight: 700; }
    .ve-grupo[hidden], .ve-error[hidden] { display: none; }
    .ve-cliente { margin: 4px 0 8px; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #64748b; }
    .ve-error { background: #fff; border: 1px solid #e1e7f2; border-radius: 8px; margin-bottom: 10px; overflow: hidden; }
    .ve-error summary { cursor: pointer; display: flex; align-items: flex-start; gap: 10px; padding: 10px 14px; list-style: none; }
    .ve-error summary::-webkit-details-marker { display: none; }
    .ve-insignia { flex: none; margin-top: 1px; font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; letter-spacing: .03em; }
    .ve-cab { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 5px; }
    .ve-titulo { font-weight: 700; font-size: 15px; }
    .ve-etiquetas { display: flex; flex-wrap: wrap; gap: 6px; }
    .ve-etiqueta { display: inline-flex; align-items: center; font-size: 13px; font-weight: 600; padding: 3px 11px; border-radius: 999px; background: #eef2f7; color: #334155; border: 1px solid #dfe5ee; }
    .ve-derecha { flex: none; display: flex; align-items: center; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
    .ve-meta { font-size: 12px; color: #64748b; white-space: nowrap; }
    .ve-boton { border: 0; cursor: pointer; font-family: inherit; display: inline-flex; align-items: center; font-size: 13.5px; font-weight: 700; padding: 7px 14px; border-radius: 8px; background: #2563eb; color: #fff !important; text-decoration: none; box-shadow: 0 1px 2px rgba(15, 23, 42, .15); white-space: nowrap; }
    .ve-boton:hover { background: #1d4ed8; }
    .ve-boton:focus-visible { outline: 2px solid #1d4ed8; outline-offset: 2px; }
    .ve-plegar { font-size: 12px; font-weight: 700; color: #2563eb; white-space: nowrap; }
    .ve-plegar::after { content: 'Ver detalle ▾'; }
    details[open] > summary .ve-plegar::after { content: 'Ocultar ▴'; }
    .ve-cuerpo { padding: 0 14px 12px; }
    /* Instructivo: lo que hay que hacer, grande y separado de la grilla. */
    .ve-instructivo { margin: 0 0 12px; padding: 10px 14px; background: #fffbeb; border: 1px solid #fcd34d; border-radius: 8px; }
    .ve-instructivo-titulo { margin: 0 0 6px; font-size: 13px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; color: #92400e; }
    .ve-ordenes { margin: 0; padding: 0; list-style: none; }
    .ve-ordenes li { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 6px 16px; padding: 7px 0; font-size: 15px; color: #1f2a37; }
    .ve-ordenes li + li { border-top: 1px dashed #fcd34d; }
    .ve-orden-comp { margin-left: 6px; font-family: Consolas, 'Cascadia Mono', monospace; font-size: 14px; color: #475569; }
    .ve-orden-cambio { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; white-space: nowrap; }
    .ve-ordenes .letra { min-width: 24px; padding: 2px 6px; font-size: 15px; border-radius: 4px; }
    .ve-flecha { font-size: 17px; font-weight: 800; color: #475569; }
    .ve-detalle { margin: 0 0 10px; color: #334155; font-size: 14.5px; line-height: 1.45; }
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
    .ve-nota { margin-left: 6px; font-family: 'Segoe UI', Arial, sans-serif; font-size: 11px; font-weight: 700; padding: 1px 7px; border-radius: 999px; background: #e8eef9; color: #1d4ed8; }
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

/** «Ventas · sin errores · 32 de 32 coinciden» por cliente, arriba de las tarjetas. */
function bloqueResumen(resumenes: readonly ResumenOperacion[], varios: boolean): string {
  if (!resumenes.length) return '';
  const filas = resumenes
    .map((r) => {
      const ok = r.a_corregir === 0;
      const estado = ok ? 'sin errores' : `${r.a_corregir} hallazgo${r.a_corregir === 1 ? '' : 's'} para corregir`;
      const quien = varios ? `<span class="ve-res-cliente">${esc(r.cliente)}</span> · ` : '';
      return `<li class="${ok ? 've-res-ok' : 've-res-mal'}"><span class="ve-res-icono" aria-hidden="true">${ok ? '✓' : '!'}</span>
        <span>${quien}<b>${esc(r.operacion)}</b>: ${estado} · ${r.coinciden} de ${r.arca} comprobantes de ARCA coinciden con Xubio</span></li>`;
    })
    .join('');
  return `<ul class="ve-res" aria-label="Resumen por operación">${filas}</ul>`;
}

/** Abre la ventana de hallazgos. Devuelve la promesa de SweetAlert2 (se resuelve al cerrarla). */
export function mostrarVentanaDeErrores(
  errores: readonly Advertencia[],
  resumenes: readonly ResumenOperacion[] = [],
  ctx: ContextoVentana = { emails: {}, periodo: '' },
) {
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
    .map(([cliente, items]) => `<section class="ve-grupo"><p class="ve-cliente">${esc(cliente)}</p>${items.map((e) => tarjeta(e, ctx)).join('')}</section>`)
    .join('');

  return Swal.fire({
    icon: n.grave ? 'error' : aCorregir ? 'warning' : 'info',
    title: total ? `Hallazgos del control (${total})` : 'Control sin hallazgos',
    html: `${ESTILOS}
      <div class="ve">
        <p class="ve-intro">${sub}</p>
        ${bloqueResumen(resumenes, new Set(resumenes.map((r) => r.cliente)).size > 1)}
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
    },
    // Sin animación: si la pestaña está atrás (mirando el Chrome del bot), el
    // navegador congela las animaciones y la ventana quedaba transparente.
    showClass: { popup: '', backdrop: 'swal2-backdrop-show', icon: '' },
    hideClass: { popup: '', backdrop: '', icon: '' },
  });
}
