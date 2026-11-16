import Swal from 'sweetalert2';
import { Advertencia, CeldaVista, ComprobanteLetraDistinta, FilaConError } from '../../core/models';

/**
 * Ventana de errores al terminar un control: qué encontró la auditoría (y qué
 * pasos fallaron), qué hacer y las filas tal como están en el Excel, con el
 * error remarcado. Solo muestra: no modifica nada.
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
  const cab = ['', ...COLUMNAS].map((c) => `<th>${esc(c)}</th>`).join('');
  const cuerpo = filas
    .map((f) => {
      const celdas = COLUMNAS.map((col) => {
        const num = col === 'Total' ? ' num' : '';
        if (col === 'Comprobante') {
          const clase = f.marca ? ` ${f.marca}` : '';
          return `<td class="comp${clase}">${f.comprobante ?? esc(texto(f.datos[col]))}</td>`;
        }
        return `<td class="${num.trim()}">${esc(texto(f.datos[col]))}</td>`;
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

function tarjeta(e: Advertencia): string {
  const meta = [e.operacion, e.total ? `${e.total} comprobante${e.total === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ');
  const mas =
    e.total > e.comprobantes.length
      ? `<p class="ve-mas">${e.comprobantes.length} de ${e.total}${e.hoja ? ` · el resto en la hoja «${esc(e.hoja)}»` : ''}</p>`
      : '';
  const letras = ordenesLetra(e);
  return `
    <details class="ve-error ve-${e.gravedad}" ${e.gravedad === 'info' ? '' : 'open'}>
      <summary>
        <span class="ve-insignia">${ETIQUETA_GRAVEDAD[e.gravedad]}</span>
        <span class="ve-titulo">${esc(e.titulo)}</span>
        <span class="ve-meta">${esc(meta)}</span>
      </summary>
      <div class="ve-cuerpo">
        <div class="ve-acciones">${e.acciones.map((a) => `<span class="ve-accion${a === 'Sin acción' ? ' ve-accion-nada' : ''}">${esc(a)}</span>`).join('')}</div>
        ${e.detalle ? `<p class="ve-detalle">${esc(e.detalle)}</p>` : ''}
        ${letras ? `<ul class="ve-ordenes">${letras}</ul>` : ''}
        ${grilla(filasDe(e))}${mas}
      </div>
    </details>`;
}

const ESTILOS = `
  <style>
    .ve { text-align: left; color: #1f2a37; font-size: 13.5px; }
    .ve-intro { margin: 0 0 6px; text-align: center; color: #475569; }
    .ve-resumen { display: flex; justify-content: center; gap: 6px; margin-bottom: 12px; }
    .ve-chip { border-radius: 999px; padding: 2px 9px; font-size: 12px; font-weight: 600; white-space: nowrap; }
    .ve-lista { max-height: min(55vh, 600px); overflow-y: auto; padding: 10px; background: #f6f8fc; border-radius: 8px; }
    .ve-cliente { margin: 4px 0 8px; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .05em; color: #64748b; }
    .ve-error { background: #fff; border: 1px solid #e1e7f2; border-radius: 8px; margin-bottom: 10px; overflow: hidden; }
    .ve-error summary { cursor: pointer; display: flex; align-items: center; gap: 10px; padding: 10px 14px; list-style: none; }
    .ve-error summary::-webkit-details-marker { display: none; }
    .ve-insignia { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 4px; text-transform: uppercase; letter-spacing: .03em; }
    .ve-titulo { font-weight: 700; flex: 1; }
    .ve-meta { font-size: 12px; color: #64748b; white-space: nowrap; }
    .ve-cuerpo { padding: 0 14px 12px; }
    .ve-acciones { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 6px; }
    .ve-accion { display: inline-flex; align-items: center; gap: 5px; background: #1d4ed8; color: #fff; font-size: 12px; font-weight: 700; padding: 3px 10px; border-radius: 999px; }
    .ve-accion::before { content: '➜'; font-size: 11px; }
    .ve-accion-nada { background: #e2e8f0; color: #475569; }
    .ve-accion-nada::before { content: '✓'; }
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
    .ve-grilla { border: 1px solid #d4d4d4; border-radius: 4px; overflow: auto; max-height: 240px; }
    .ve-grilla table { border-collapse: collapse; width: 100%; font: 12px Calibri, 'Segoe UI', Arial, sans-serif; white-space: nowrap; }
    .ve-grilla th { position: sticky; top: 0; background: #f3f3f3; color: #444; font-weight: 600; text-align: left; padding: 4px 8px; border-bottom: 1px solid #d4d4d4; border-right: 1px solid #e4e4e4; }
    .ve-grilla td { padding: 4px 8px; border-bottom: 1px solid #ececec; border-right: 1px solid #f0f0f0; }
    .ve-grilla td.num { text-align: right; font-variant-numeric: tabular-nums; }
    .ve-grilla td.origen { font-weight: 700; font-size: 11px; color: #fff; text-align: center; width: 1%; }
    .ve-grilla td.origen-arca { background: #1b2f57; }
    .ve-grilla td.origen-xubio { background: #217346; }
    .ve-grilla td.comp { font-family: Consolas, 'Cascadia Mono', monospace; }
    .ve-grilla td.comp.error { background: #fdeaea; box-shadow: inset 0 0 0 1.5px #c53030; }
    .ve-grilla td.comp.ok { background: #e7f5ec; box-shadow: inset 0 0 0 1.5px #15803d; }
    .letra { display: inline-block; min-width: 16px; text-align: center; border-radius: 3px; color: #fff; font-weight: 700; padding: 0 3px; }
    .letra-error { background: #c53030; }
    .letra-ok { background: #15803d; }
  </style>`;

/** Cuántos errores hay por gravedad. */
export function contarErrores(errores: readonly Advertencia[]): Record<Advertencia['gravedad'], number> {
  const n = { grave: 0, corregir: 0, info: 0 };
  for (const e of errores) n[e.gravedad] = (n[e.gravedad] ?? 0) + 1;
  return n;
}

/** Abre la ventana de errores. Devuelve la promesa de SweetAlert2 (se resuelve al cerrarla). */
export function mostrarVentanaDeErrores(errores: readonly Advertencia[]) {
  const n = contarErrores(errores);
  const aCorregir = n.grave + n.corregir;
  const ordenados = [...errores].sort((a, b) => ORDEN_GRAVEDAD[a.gravedad] - ORDEN_GRAVEDAD[b.gravedad]);
  const porCliente = new Map<string, Advertencia[]>();
  for (const e of ordenados) porCliente.set(e.cliente, [...(porCliente.get(e.cliente) ?? []), e]);

  const sub = n.grave
    ? 'El control terminó con errores graves: el resultado no es confiable.'
    : aCorregir
      ? 'El control se hizo correctamente. Hay datos para corregir.'
      : 'El control se hizo correctamente.';
  const chips = (['grave', 'corregir', 'info'] as const)
    .filter((g) => n[g])
    .map((g) => `<span class="ve-chip ve-chip-${g}">${n[g]} ${ETIQUETA_GRAVEDAD[g].toLowerCase()}</span>`)
    .join('');
  const lista = [...porCliente.entries()]
    .map(([cliente, items]) => `<p class="ve-cliente">${esc(cliente)}</p>${items.map(tarjeta).join('')}`)
    .join('');

  return Swal.fire({
    icon: n.grave ? 'error' : aCorregir ? 'warning' : 'info',
    title: aCorregir ? `Errores del control (${aCorregir})` : 'Control sin errores',
    html: `${ESTILOS}
      <div class="ve">
        <p class="ve-intro">${sub}</p>
        <div class="ve-resumen">${chips}</div>
        <div class="ve-lista">${lista}</div>
      </div>`,
    width: 'min(980px, 96vw)',
    confirmButtonText: 'Entendido',
    confirmButtonColor: '#2563eb',
    focusConfirm: true,
    // Sin animación: si la pestaña está atrás (mirando el Chrome del bot), el
    // navegador congela las animaciones y la ventana quedaba transparente.
    showClass: { popup: '', backdrop: 'swal2-backdrop-show', icon: '' },
    hideClass: { popup: '', backdrop: '', icon: '' },
  });
}
