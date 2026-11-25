import { ComprobanteExtraido, ParteComprobante } from '../../core/models';

/**
 * Reglas de la revisión de comprobantes leídos (PDF, fotos, CSV, planillas).
 * Son las mismas que valida el backend (backend/app/extraccion/modelo.py): acá se
 * recalculan al editar, sin ir al servidor.
 */

export const TOTAL = 'Imp. Total';
export const NETO_TOTAL = 'Imp. Neto Gravado Total';
export const TOTAL_IVA = 'Total IVA';
const ALICUOTAS = ['0', '2,5', '5', '10,5', '21', '27'] as const;
const neto = (a: string) => `Imp. Neto Gravado IVA ${a}%`;
const iva = (a: string) => `IVA ${a}%`;

/** Los importes del CSV de Mis Comprobantes, en el orden de la planilla de revisión. */
export const CAMPOS_IMPORTE: { clave: string; etiqueta: string }[] = [
  ...ALICUOTAS.flatMap((a) => [
    { clave: neto(a), etiqueta: `Neto ${a}%` },
    ...(a === '0' ? [] : [{ clave: iva(a), etiqueta: `IVA ${a}%` }]),
  ]),
  { clave: 'Imp. Neto No Gravado', etiqueta: 'No gravado' },
  { clave: 'Imp. Op. Exentas', etiqueta: 'Exento' },
  { clave: 'Otros Tributos', etiqueta: 'Otros tributos' },
  { clave: NETO_TOTAL, etiqueta: 'Neto gravado total' },
  { clave: TOTAL_IVA, etiqueta: 'Total IVA' },
  { clave: TOTAL, etiqueta: 'Total' },
];

/** Códigos de comprobante de ARCA (los que se ven en el selector). */
export const TIPOS: { codigo: number; nombre: string }[] = [
  [1, 'Factura A'], [2, 'Nota de Débito A'], [3, 'Nota de Crédito A'], [4, 'Recibo A'],
  [6, 'Factura B'], [7, 'Nota de Débito B'], [8, 'Nota de Crédito B'], [9, 'Recibo B'],
  [11, 'Factura C'], [12, 'Nota de Débito C'], [13, 'Nota de Crédito C'], [15, 'Recibo C'],
  [19, 'Factura E'], [20, 'Nota de Débito E'], [21, 'Nota de Crédito E'],
  [51, 'Factura M'], [52, 'Nota de Débito M'], [53, 'Nota de Crédito M'], [54, 'Recibo M'],
  [81, 'Tique Factura A'], [82, 'Tique Factura B'], [83, 'Tique'], [111, 'Tique Factura C'],
  [201, 'FCE MiPyMEs A'], [202, 'ND FCE MiPyMEs A'], [203, 'NC FCE MiPyMEs A'],
  [206, 'FCE MiPyMEs B'], [207, 'ND FCE MiPyMEs B'], [208, 'NC FCE MiPyMEs B'],
  [211, 'FCE MiPyMEs C'], [212, 'ND FCE MiPyMEs C'], [213, 'NC FCE MiPyMEs C'],
].map(([codigo, nombre]) => ({ codigo: codigo as number, nombre: nombre as string }));

export const METODOS: Record<ComprobanteExtraido['metodo'], { texto: string; ayuda: string }> = {
  qr: { texto: 'QR', ayuda: 'Leído del QR de ARCA: fecha, número, CUIT, total y CAE son exactos.' },
  pdf: { texto: 'PDF', ayuda: 'Leído del texto del PDF.' },
  ocr: { texto: 'OCR', ayuda: 'Leído de la imagen con OCR: revisá números e importes.' },
  planilla: { texto: 'Planilla', ayuda: 'Leído de la planilla.' },
  manual: { texto: 'A mano', ayuda: 'No se pudo leer: completalo a mano o sacalo.' },
};

/** En Ventas la contraparte es el receptor; en Compras, el emisor (el proveedor). */
export function contraparte(c: ComprobanteExtraido): ParteComprobante {
  return c.operacion === 'Compras' ? c.emisor : c.receptor;
}

/** El comprobante con la contraparte cambiada (en la parte que corresponde a la operación). */
export function conContraparte(c: ComprobanteExtraido, cambios: Partial<ParteComprobante>): ComprobanteExtraido {
  const rol = c.operacion === 'Compras' ? 'emisor' : 'receptor';
  return { ...c, [rol]: { ...c[rol], ...cambios } };
}

export function cuitValido(cuit: string): boolean {
  const c = (cuit ?? '').replace(/\D/g, '');
  if (c.length !== 11) return false;
  const pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2];
  const resto = 11 - (pesos.reduce((s, p, i) => s + p * Number(c[i]), 0) % 11);
  const dv = resto === 11 ? 0 : resto === 10 ? 9 : resto;
  return dv === Number(c[10]);
}

const suma = (c: ComprobanteExtraido, claves: string[]) => claves.reduce((s, k) => s + (c.importes[k] ?? 0), 0);
const redondo = (n: number) => Math.round(n * 100) / 100;

/** Neto gravado total y total IVA a partir de las alícuotas (lo que el backend recalcula al preparar). */
export function recalcularTotales(c: ComprobanteExtraido): ComprobanteExtraido {
  const netos = ALICUOTAS.map(neto);
  const ivas = ALICUOTAS.filter((a) => a !== '0').map(iva);
  const importes = { ...c.importes };
  if (netos.some((k) => importes[k] != null)) importes[NETO_TOTAL] = redondo(suma(c, netos));
  if (ivas.some((k) => importes[k] != null)) importes[TOTAL_IVA] = redondo(suma(c, ivas));
  return { ...c, importes };
}

export function formatoImporte(n: number | null | undefined): string {
  return n == null ? '' : n.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Lo que hay que mirar antes de subirlo. Vacío = listo. */
export function validarComprobante(c: ComprobanteExtraido): string[] {
  const avisos: string[] = [];
  if (!c.operacion) avisos.push('Falta elegir si es venta o compra.');
  if (!c.cliente) avisos.push('Falta elegir el cliente (la empresa en Xubio).');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(c.fecha ?? '')) avisos.push('Falta la fecha.');
  if (c.tipo == null) avisos.push('Falta el tipo de comprobante.');
  else if (!TIPOS.some((t) => t.codigo === c.tipo)) avisos.push(`Tipo de comprobante ${c.tipo} desconocido.`);
  if (!c.punto_venta) avisos.push('Falta el punto de venta.');
  if (!c.numero) avisos.push('Falta el número.');
  if (c.importes[TOTAL] == null) avisos.push('Falta el importe total.');
  if (c.cae && c.cae.length !== 14) avisos.push('El CAE debería tener 14 dígitos.');
  if (!c.cae) avisos.push('Sin CAE: Xubio puede no aceptarlo en «Importar desde Mis Comprobantes».');
  if (c.operacion) {
    const p = contraparte(c);
    const quien = c.operacion === 'Compras' ? 'del proveedor' : 'del cliente';
    if (!p.doc_nro && p.doc_tipo !== 99) avisos.push(`Falta el CUIT ${quien}.`);
    else if ((p.doc_tipo == null || p.doc_tipo === 80 || p.doc_tipo === 86) && p.doc_nro && !cuitValido(p.doc_nro)) {
      avisos.push(`El CUIT ${quien} (${p.doc_nro}) no es válido.`);
    }
  }
  const total = c.importes[TOTAL];
  const partes = [NETO_TOTAL, 'Imp. Neto No Gravado', 'Imp. Op. Exentas', 'Otros Tributos', TOTAL_IVA];
  if (total != null && partes.some((k) => c.importes[k] != null)) {
    const s = redondo(suma(c, partes));
    if (Math.abs(s - total) > Math.max(1, Math.abs(total) * 0.001)) {
      avisos.push(`Los importes no suman el total (suman ${formatoImporte(s)}, total ${formatoImporte(total)}).`);
    }
  }
  return avisos;
}

/** Los avisos que no impiden subirlo (sin CAE, CUIT dudoso…) vs. los que sí. */
export function bloquea(c: ComprobanteExtraido): boolean {
  return !c.operacion || !c.cliente;
}
