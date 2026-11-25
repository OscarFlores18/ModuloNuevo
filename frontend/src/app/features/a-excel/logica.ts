/**
 * «Conversor De Facturas»: lo que no es pantalla (números, campos, verificaciones).
 * Las cuentas de verdad (qué está mal y qué sugerir) las hace el backend:
 * backend/app/extraccion/a_excel.py → `verificar`.
 */
import { ItemLeido, LecturaExcel, NivelVerificacion, Verificacion } from '../../core/models';

/** Lo que se acepta subir (lo mismo que `FORMATOS` en a_excel.py). */
export const FORMATOS_A_EXCEL = /\.(xlsx|xlsm|xls|csv|txt|pdf|jpe?g|png|webp|bmp|tiff?)$/i;
export const ACEPTA = '.xlsx,.xlsm,.xls,.csv,.txt,.pdf,.jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff';

export const CAMPOS_ENCABEZADO: { id: string; etiqueta: string }[] = [
  { id: 'emisor', etiqueta: 'Emisor' },
  { id: 'emisor_cuit', etiqueta: 'CUIT del emisor' },
  { id: 'receptor', etiqueta: 'Receptor' },
  { id: 'receptor_cuit', etiqueta: 'CUIT del receptor' },
  { id: 'tipo', etiqueta: 'Tipo' },
  { id: 'numero', etiqueta: 'Número' },
  { id: 'fecha', etiqueta: 'Fecha (DD/MM/AAAA)' },
  { id: 'cae', etiqueta: 'CAE' },
];

export const CAMPOS_TOTALES: { id: string; etiqueta: string }[] = [
  { id: 'neto_gravado', etiqueta: 'Neto gravado' },
  { id: 'no_gravado', etiqueta: 'No gravado' },
  { id: 'exento', etiqueta: 'Exento' },
  { id: 'iva', etiqueta: 'IVA' },
  { id: 'otros_tributos', etiqueta: 'Otros tributos' },
  { id: 'total', etiqueta: 'Total' },
];

/** «83.974,75», «83974.75», «$ 1.210» o vacío (null). */
export function aNumero(texto: string): number | null {
  let t = texto.replace(/[$\s]/g, '');
  if (!t) return null;
  const negativo = /^-|^\(.*\)$/.test(t);
  t = t.replace(/[()-]/g, '');
  if (t.includes(',') && t.includes('.')) {
    t = t.lastIndexOf(',') > t.lastIndexOf('.') ? t.replace(/\./g, '').replace(',', '.') : t.replace(/,/g, '');
  } else if (t.includes(',')) {
    t = t.replace(',', '.');
  } else if (/^\d{1,3}(\.\d{3})+$/.test(t)) {
    t = t.replace(/\./g, '');
  }
  const n = Number(t);
  return Number.isFinite(n) ? (negativo ? -n : n) : null;
}

/** 83974.75 → «83.974,75». */
export function aTexto(n: number | null | undefined): string {
  return n === null || n === undefined ? '' : n.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** El peor estado de un campo (para pintar el input). */
export function nivelDe(verificaciones: Verificacion[], campo: string): NivelVerificacion | null {
  const del = verificaciones.filter((v) => v.campo === campo);
  if (del.some((v) => v.nivel === 'error')) return 'error';
  if (del.some((v) => v.nivel === 'aviso')) return 'aviso';
  return del.length ? 'ok' : null;
}

export function contar(lectura: LecturaExcel, nivel: NivelVerificacion): number {
  return (lectura.verificaciones ?? []).filter((v) => v.nivel === nivel).length;
}

/** Sin la duda de un campo: una vez que la persona lo corrigió, ya está mirado. */
export function sinDuda(lectura: LecturaExcel, campo: string): LecturaExcel {
  if (!(campo in lectura.dudas)) return lectura;
  const dudas = { ...lectura.dudas };
  delete dudas[campo];
  return { ...lectura, dudas };
}

/** Aplica la sugerencia de una verificación al campo que corresponde. */
export function usarSugerencia(lectura: LecturaExcel, v: Verificacion): LecturaExcel {
  if (v.sugerencia === null || v.sugerencia === undefined) return lectura;
  if (CAMPOS_TOTALES.some((c) => c.id === v.campo)) {
    return sinDuda({ ...lectura, totales: { ...lectura.totales, [v.campo]: Number(v.sugerencia) } }, v.campo);
  }
  return sinDuda({ ...lectura, encabezado: { ...lectura.encabezado, [v.campo]: String(v.sugerencia) } }, v.campo);
}

export function itemVacio(): ItemLeido {
  return { codigo: '', descripcion: '', cantidad: null, precio: null, alicuota: 21, importe: null };
}

export function sumaItems(items: ItemLeido[]): number {
  return Math.round(items.reduce((s, x) => s + (x.importe ?? 0), 0) * 100) / 100;
}
