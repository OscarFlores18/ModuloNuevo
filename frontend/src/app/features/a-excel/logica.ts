/**
 * «Conversor De Facturas»: lo que no es pantalla (números, campos, verificaciones).
 * Las cuentas de verdad (qué está mal y qué sugerir) las hace el backend:
 * backend/app/extraccion/a_excel.py → `verificar`.
 */
import { ItemLeido, LecturaExcel, NivelVerificacion, Verificacion } from '../../core/models';

/** Lo que se acepta subir (lo mismo que `FORMATOS` en a_excel.py). */
export const FORMATOS_A_EXCEL = /\.(xlsx|xlsm|xls|csv|txt|pdf|jpe?g|png|webp|bmp|tiff?)$/i;
export const ACEPTA = '.xlsx,.xlsm,.xls,.csv,.txt,.pdf,.jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff';

/** Lo editable del encabezado. El CAE no va: no se muestra ni se modifica (queda el leído). */
export const CAMPOS_ENCABEZADO: { id: string; etiqueta: string }[] = [
  { id: 'emisor', etiqueta: 'Emisor' },
  { id: 'emisor_cuit', etiqueta: 'CUIT del emisor' },
  { id: 'receptor', etiqueta: 'Receptor' },
  { id: 'receptor_cuit', etiqueta: 'CUIT del receptor' },
  { id: 'tipo', etiqueta: 'Tipo' },
  { id: 'numero', etiqueta: 'Número' },
  { id: 'fecha', etiqueta: 'Fecha (DD/MM/AAAA)' },
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

// --- Para el estudio: de qué empresa es la factura y cómo queda desde su lado ---------

export type Operacion = 'Compras' | 'Ventas';

/** Una empresa del estudio, con las CUIT que la identifican (la representada y la de login). */
export interface EmpresaEstudio {
  nombre: string;
  cuits: string[];
}

/** La factura vista desde la empresa del estudio. */
export interface FacturaDelEstudio {
  empresa: string;
  operacion: Operacion;
  /** Cómo se detectó: por la CUIT del receptor, del emisor, o elegida a mano. */
  origen: 'receptor' | 'emisor' | 'manual';
  /** Proveedor (compra) o cliente (venta). */
  contraparte: { nombre: string; cuit: string };
}

export const soloDigitos = (s: string | undefined | null) => (s ?? '').replace(/\D/g, '');

/**
 * La empresa del estudio de esta factura: si es el receptor, es una compra; si es
 * el emisor, una venta. Primero el receptor (lo más común: facturas de proveedores).
 */
export function detectarEmpresa(encabezado: Record<string, string>, empresas: EmpresaEstudio[]): FacturaDelEstudio | null {
  const receptor = soloDigitos(encabezado['receptor_cuit']);
  const emisor = soloDigitos(encabezado['emisor_cuit']);
  const de = (cuit: string) => (cuit.length === 11 ? empresas.find((e) => e.cuits.map(soloDigitos).includes(cuit)) : undefined);
  const comoReceptor = de(receptor);
  if (comoReceptor) return adaptar(encabezado, comoReceptor.nombre, 'Compras', 'receptor');
  const comoEmisor = de(emisor);
  if (comoEmisor) return adaptar(encabezado, comoEmisor.nombre, 'Ventas', 'emisor');
  return null;
}

/** La factura desde el lado de la empresa: en una compra la contraparte es el emisor; en una venta, el receptor. */
export function adaptar(encabezado: Record<string, string>, empresa: string, operacion: Operacion, origen: FacturaDelEstudio['origen']): FacturaDelEstudio {
  const lado = operacion === 'Compras' ? 'emisor' : 'receptor';
  return { empresa, operacion, origen, contraparte: { nombre: encabezado[lado] ?? '', cuit: soloDigitos(encabezado[`${lado}_cuit`]) } };
}

/** «30-71080988-3». */
export function cuitConGuiones(cuit: string): string {
  const d = soloDigitos(cuit);
  return d.length === 11 ? `${d.slice(0, 2)}-${d.slice(2, 10)}-${d.slice(10)}` : cuit;
}

/** El texto leído sin el número de CAE (no se muestra). */
export function sinCae(texto: string): string {
  return texto.replace(/(C\.?\s?A\.?\s?E\.?[^\d\n]{0,20})\d{14}/gi, '$1(oculto)');
}

// --- Productos de cada empresa (se recuerdan en esta computadora) ---------------------

const CLAVE_PRODUCTOS = 'cf-productos-por-empresa';

/** Los de siempre, según la operación, para arrancar. */
export const PRODUCTOS_BASE: Record<Operacion, string[]> = {
  Compras: ['Mercaderías', 'Servicios', 'Gastos de limpieza', 'Refrigerio', 'Combustible', 'Honorarios', 'Alquiler'],
  Ventas: ['Servicios profesionales', 'Honorarios', 'Venta de mercaderías', 'Abono mensual'],
};

function leerProductos(): Record<string, string[]> {
  try {
    const crudo = JSON.parse(localStorage.getItem(CLAVE_PRODUCTOS) ?? '{}');
    return crudo && typeof crudo === 'object' ? crudo : {};
  } catch {
    return {};
  }
}

/** Primero los que ya usó esa empresa (el último arriba), después los de siempre. */
export function productosSugeridos(empresa: string, operacion: Operacion): string[] {
  const usados = empresa ? (leerProductos()[`${empresa}|${operacion}`] ?? []) : [];
  return [...new Set([...usados, ...PRODUCTOS_BASE[operacion]])].slice(0, 10);
}

export function recordarProducto(empresa: string, producto: string, operacion: Operacion = 'Compras'): void {
  try {
    const todos = leerProductos();
    const clave = `${empresa}|${operacion}`;
    todos[clave] = [producto, ...(todos[clave] ?? []).filter((p) => p !== producto)].slice(0, 12);
    localStorage.setItem(CLAVE_PRODUCTOS, JSON.stringify(todos));
  } catch {
    /* sin almacenamiento: no se recuerda */
  }
}
