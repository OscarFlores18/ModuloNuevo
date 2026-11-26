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
  { id: 'impuestos_internos', etiqueta: 'Impuestos internos' },
  { id: 'percepcion_iibb', etiqueta: 'Percepción IIBB Bs. As.' },
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
  origen: 'receptor' | 'emisor' | 'manual' | 'trabajo';
  /** Proveedor (compra) o cliente (venta). */
  contraparte: { nombre: string; cuit: string };
}

export const soloDigitos = (s: string | undefined | null) => (s ?? '').replace(/\D/g, '');

/**
 * La empresa del estudio de esta factura: si es el receptor, es una compra; si es
 * el emisor, una venta. Primero el receptor (lo más común: facturas de proveedores).
 */
export function detectarEmpresa(encabezado: Record<string, string>, empresas: EmpresaEstudio[]): FacturaDelEstudio | null {
  const lados: ['receptor' | 'emisor', Operacion][] = [['receptor', 'Compras'], ['emisor', 'Ventas']];
  // 1) La CUIT tal cual; 2) la CUIT con 1 o 2 dígitos mal leídos (OCR); 3) el nombre.
  const pruebas: ((e: EmpresaEstudio, cuit: string, nombre: string) => boolean)[] = [
    (e, cuit) => cuit.length === 11 && e.cuits.map(soloDigitos).includes(cuit),
    (e, cuit) => cuit.length === 11 && e.cuits.some((c) => digitosDistintos(soloDigitos(c), cuit) <= 2),
    (e, _, nombre) => mismoNombre(e.nombre, nombre),
  ];
  for (const prueba of pruebas) {
    for (const [lado, operacion] of lados) {
      const cuit = soloDigitos(encabezado[`${lado}_cuit`]);
      const e = empresas.find((x) => prueba(x, cuit, encabezado[lado] ?? ''));
      if (e) return adaptar(encabezado, e.nombre, operacion, lado);
    }
  }
  return null;
}

/** Cuántos dígitos cambian entre dos CUIT del mismo largo. */
export function digitosDistintos(a: string, b: string): number {
  if (a.length !== b.length) return 99;
  return [...a].filter((d, i) => d !== b[i]).length;
}

const SOCIEDAD = /\b(s\.?\s?a\.?\s?s?|s\.?\s?r\.?\s?l|s\.?\s?a\.?\s?u|srl|sas|sau|sa)\b\.?/g;
/** «Vendedora Prueba S.A.» y «VENDEDORA PRUEBA SA» son la misma empresa. */
export function mismoNombre(a: string, b: string): boolean {
  const n = (s: string) => s.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(SOCIEDAD, ' ').replace(/[^a-z0-9]+/g, ' ').trim();
  const x = n(a), y = n(b);
  return x.length >= 4 && y.length >= 4 && (x === y || x.includes(y) || y.includes(x));
}

/** La empresa con la que se viene trabajando: queda elegida si la factura no la nombra. */
const CLAVE_TRABAJO = 'cf-empresa-de-trabajo';
export function empresaDeTrabajo(): string {
  try { return localStorage.getItem(CLAVE_TRABAJO) ?? ''; } catch { return ''; }
}
export function recordarEmpresaDeTrabajo(nombre: string): void {
  try { if (nombre) localStorage.setItem(CLAVE_TRABAJO, nombre); } catch { /* sin almacenamiento */ }
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
  const clave = (x: string) => x.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim();
  const vistos = new Set<string>();
  return [...usados, ...PRODUCTOS_BASE[operacion]].filter((x) => !vistos.has(clave(x)) && !!vistos.add(clave(x))).slice(0, 10);
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
