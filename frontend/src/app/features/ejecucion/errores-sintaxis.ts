import Swal from 'sweetalert2';
import { Advertencia, ComprobanteLetraDistinta, CeldaVista } from '../../core/models';

/**
 * Aviso al terminar un control que encontró comprobantes con la letra mal
 * cargada en Xubio («error de sintaxis»). Solo muestra: dibuja las dos filas
 * como si fueran la planilla de Excel, con la celda del error remarcada.
 */

const LETRAS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';

function esc(texto: unknown): string {
  return String(texto ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function valor(c: CeldaVista): string {
  if (c.valor === null || c.valor === undefined) return '';
  if (typeof c.valor === 'number') {
    return c.valor.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  return String(c.valor);
}

type Marca = 'error' | 'correcto' | null;

/** Una «hoja» de Excel de una sola fila, con la celda indicada remarcada. */
function hoja(titulo: string, fila: CeldaVista[], marcar: (c: CeldaVista) => { marca: Marca; html?: string }): string {
  const cab = fila.map((_, i) => `<th>${LETRAS[i] ?? ''}</th>`).join('');
  const nombres = fila.map((c) => `<td class="xl-nombre">${esc(c.columna)}</td>`).join('');
  const datos = fila
    .map((c) => {
      const { marca, html } = marcar(c);
      const numero = typeof c.valor === 'number' ? ' xl-num' : '';
      return `<td class="${marca ? `xl-${marca}` : ''}${numero}">${html ?? esc(valor(c))}</td>`;
    })
    .join('');
  return `
    <div class="xl">
      <div class="xl-scroll">
        <table>
          <thead><tr><th class="xl-esquina"></th>${cab}</tr></thead>
          <tbody>
            <tr><th>1</th>${nombres}</tr>
            <tr><th>2</th>${datos}</tr>
          </tbody>
        </table>
      </div>
      <div class="xl-pestanas"><span class="xl-pestana">${esc(titulo)}</span></div>
    </div>`;
}

/** «A-00002-00000006» con la letra remarcada. */
function comprobanteConLetra(comprobante: string, clase: 'error' | 'correcto'): string {
  const [letra, ...resto] = comprobante.split('-');
  return `<mark class="xl-letra-${clase}">${esc(letra)}</mark>-${esc(resto.join('-'))}`;
}

function bloque(a: Advertencia, c: ComprobanteLetraDistinta): string {
  const arca = hoja('Mis Comprobantes (ARCA)', c.fila_arca, (celda) =>
    celda.columna === 'Comprobante'
      ? { marca: 'correcto', html: comprobanteConLetra(c.comprobante_arca, 'correcto') }
      : { marca: null },
  );
  const xubio = hoja('Libro IVA (Xubio)', c.fila_xubio, (celda) =>
    celda.columna === 'Comprobante'
      ? { marca: 'error', html: comprobanteConLetra(c.comprobante_xubio, 'error') }
      : { marca: null },
  );
  return `
    <section class="se-bloque">
      <p class="se-titulo"><b>${esc(c.proveedor)}</b> · ${esc(a.cliente)} · ${esc(a.operacion)}</p>
      ${arca}
      ${xubio}
      <p class="se-nota">
        En ARCA es <mark class="xl-letra-correcto">${esc(c.letra_arca)}</mark> y en Xubio quedó cargado como
        <mark class="xl-letra-error">${esc(c.letra_xubio)}</mark>. Corregí la letra del comprobante en Xubio.
      </p>
    </section>`;
}

const ESTILOS = `
  <style>
    .se-intro { margin: 0 0 12px; text-align: left; font-size: 14px; color: #1f2a37; }
    .se-bloque { text-align: left; margin-top: 14px; padding-top: 12px; border-top: 1px solid #d5deee; }
    .se-titulo { margin: 0 0 8px; font-size: 14px; color: #1f2a37; }
    .se-nota { margin: 8px 0 0; font-size: 13px; color: #1f2a37; }
    .xl { border: 1px solid #c8ced6; border-radius: 4px; margin-bottom: 10px; background: #fff; overflow: hidden; }
    .xl-scroll { overflow-x: auto; }
    .xl table { border-collapse: collapse; font: 12px Calibri, 'Segoe UI', Arial, sans-serif; color: #1f1f1f; white-space: nowrap; width: 100%; }
    .xl th { background: #f3f3f3; color: #555; font-weight: normal; border: 1px solid #d4d4d4; padding: 2px 8px; text-align: center; min-width: 26px; }
    .xl td { border: 1px solid #e1e1e1; padding: 3px 8px; text-align: left; }
    .xl td.xl-num { text-align: right; }
    .xl td.xl-nombre { font-weight: bold; background: #fafafa; }
    .xl td.xl-error { background: #fde2e2; outline: 2px solid #c53030; outline-offset: -2px; font-weight: bold; }
    .xl td.xl-correcto { background: #e3f4e8; outline: 2px solid #15803d; outline-offset: -2px; font-weight: bold; }
    .xl-pestanas { background: #f3f3f3; border-top: 1px solid #d4d4d4; padding: 0 6px; }
    .xl-pestana { display: inline-block; background: #fff; color: #217346; font: 600 12px 'Segoe UI', Arial, sans-serif; padding: 3px 12px; border-bottom: 2px solid #217346; }
    mark.xl-letra-error { background: #c53030; color: #fff; padding: 0 4px; border-radius: 3px; }
    mark.xl-letra-correcto { background: #15803d; color: #fff; padding: 0 4px; border-radius: 3px; }
  </style>`;

/** Abre el aviso. Devuelve la promesa de SweetAlert2 (se resuelve al cerrarlo). */
export function mostrarErroresDeSintaxis(advertencias: readonly Advertencia[]) {
  const letras = advertencias.filter((a) => a.tipo === 'letra_distinta');
  const total = letras.reduce((n, a) => n + a.comprobantes.length, 0);
  const html =
    ESTILOS +
    `<p class="se-intro">El control se hizo correctamente, pero se ${total === 1 ? 'encontró un error' : `encontraron ${total} errores`} de sintaxis:
      ${total === 1 ? 'un comprobante tiene' : 'hay comprobantes con'} la letra distinta entre ARCA y Xubio.
      <b>Tenés que ir a modificarlo a Xubio.</b></p>` +
    letras.map((a) => a.comprobantes.map((c) => bloque(a, c)).join('')).join('');

  return Swal.fire({
    icon: 'warning',
    title: 'Control terminado con un error de sintaxis',
    html,
    width: 'min(900px, 96vw)',
    confirmButtonText: 'Entendido',
    confirmButtonColor: '#2563eb',
    focusConfirm: true,
  });
}
