/**
 * «Conversor De Facturas»: lo que no es pantalla (números, campos, verificaciones).
 * Las cuentas de verdad (qué está mal y qué sugerir) las hace el backend:
 * backend/app/extraccion/a_excel.py → `verificar`.
 */
import { AlicuotaIva, ItemLeido, TributoLeido, LecturaExcel, NivelVerificacion, Verificacion } from '../../core/models';

/** Lo que se acepta subir (lo mismo que `FORMATOS` en a_excel.py). */
export const FORMATOS_A_EXCEL = /\.(xlsx|xlsm|xls|csv|txt|pdf|jpe?g|png|webp|bmp|tiff?)$/i;
export const ACEPTA = '.xlsx,.xlsm,.xls,.csv,.txt,.pdf,.jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff';

/**
 * El encabezado, en una grilla de 6 columnas (`ancho`: cuántas ocupa). Fila 1:
 * emisor, su CUIT, receptor, su CUIT; fila 2: tipo, número y fecha. Sin CAE: los
 * tiques de controlador fiscal no lo llevan.
 */
export const CAMPOS_ENCABEZADO: { id: string; etiqueta: string; ancho: number; ayuda?: string }[] = [
  { id: 'emisor', etiqueta: 'Emisor', ancho: 2 },
  { id: 'emisor_cuit', etiqueta: 'CUIT del emisor', ancho: 1 },
  { id: 'receptor', etiqueta: 'Receptor', ancho: 2 },
  { id: 'receptor_cuit', etiqueta: 'CUIT del receptor', ancho: 1 },
  { id: 'tipo', etiqueta: 'Tipo de comprobante', ancho: 2 },
  { id: 'numero', etiqueta: 'Número', ancho: 2, ayuda: '00001-00000001' },
  { id: 'fecha', etiqueta: 'Fecha', ancho: 2, ayuda: 'DD/MM/AAAA' },
];

/** Bases y totales (el IVA va aparte, por alícuota). */
export const CAMPOS_TOTALES: { id: string; etiqueta: string }[] = [
  { id: 'neto_gravado', etiqueta: 'Neto gravado' },
  { id: 'no_gravado', etiqueta: 'No gravado' },
  { id: 'exento', etiqueta: 'Exento' },
  { id: 'impuestos_internos', etiqueta: 'Impuestos internos' },
  { id: 'percepcion_iibb', etiqueta: 'Percepción IIBB Bs. As.' },
  { id: 'otros_tributos', etiqueta: 'Otros tributos' },
  { id: 'total', etiqueta: 'Total' },
];

/** Todos los importes de `totales` (para «Usar»). */
const TOTALES = ['neto_gravado', 'no_gravado', 'exento', 'iva', 'impuestos_internos', 'percepcion_iibb', 'otros_tributos', 'total'];

/** Las alícuotas de IVA que se pueden elegir. */
export const ALICUOTAS_IVA = [21, 10.5, 27, 5, 2.5, 0];

const TOLERANCIA = 0.05;

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
  // «iva_N»: el IVA de la fila N del desglose. «iva» con una sola fila (o ninguna): esa fila.
  const fila = /^iva_(\d+)$/.exec(v.campo)?.[1];
  const desglose = lectura.iva_alicuotas ?? [];
  if (fila !== undefined || (v.campo === 'iva' && desglose.length <= 1)) {
    const i = fila !== undefined ? Number(fila) : 0;
    const filas = desglose.length ? desglose : [{ alicuota: null, base: lectura.totales['neto_gravado'] ?? null, importe: null }];
    return conDesglose(lectura, filas.map((f, j) => (j === i ? { ...f, importe: Number(v.sugerencia) } : f)));
  }
  if (TOTALES.includes(v.campo)) {
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

/** El desglose de IVA nuevo, con `totales.iva` igual a su suma. */
export function conDesglose(lectura: LecturaExcel, filas: AlicuotaIva[]): LecturaExcel {
  const iva = filas.length ? redondear(filas.reduce((s, f) => s + (f.importe ?? 0), 0)) : null;
  return sinDuda({ ...lectura, iva_alicuotas: filas, totales: { ...lectura.totales, iva } }, 'iva');
}

type GrupoTributo = 'impuestos_internos' | 'percepcion_iibb' | 'otros_tributos';

/**
 * A qué total va una fila de otros tributos (lo mismo que `grupo_tributo` en
 * a_excel.py): impuestos internos, percepción IIBB Bs. As. o el resto.
 */
export function grupoTributo(t: TributoLeido): GrupoTributo {
  const d = t.descripcion.toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, '');
  if (/\bimp(?:uestos?|\.)?\s*int(?:ernos?|\.)?\b/.test(d)) return 'impuestos_internos';
  if (/\bperc\w*\.?.*\b(?:i[il1t]bb|ii\.?\s*bb|ing(?:resos)?\.?\s*brutos|arba)\b/.test(d) && !/\b(?:caba|ciudad|agip|capital)\b/.test(d)) {
    return 'percepcion_iibb';
  }
  return 'otros_tributos';
}

/**
 * Los otros tributos nuevos. Con filas, cada una suma una sola vez en su total
 * (impuestos internos, percepción IIBB Bs. As. u otros tributos): si no, la
 * percepción contaría dos veces en el total de la factura.
 */
export function conTributos(lectura: LecturaExcel, filas: TributoLeido[]): LecturaExcel {
  const totales = { ...lectura.totales };
  // Con renglones (o si se acaba de borrar el último), los renglones mandan: lo borrado deja de sumar.
  if (filas.length || (lectura.tributos ?? []).length) {
    const grupos = new Map<GrupoTributo, number>();
    for (const f of filas) grupos.set(grupoTributo(f), (grupos.get(grupoTributo(f)) ?? 0) + (f.importe ?? 0));
    for (const g of ['impuestos_internos', 'percepcion_iibb', 'otros_tributos'] as const) {
      totales[g] = grupos.get(g) ? redondear(grupos.get(g)!) : null;
    }
  }
  return sinDuda({ ...lectura, tributos: filas, totales }, 'otros_tributos');
}

/** 21 → «IVA 21%», 10.5 → «IVA 10,5%». */
export function nombreAlicuota(a: number | null): string {
  return a === null ? 'IVA' : `IVA ${String(a).replace('.', ',')}%`;
}

/** Una alícuota que no esté ya en el desglose (para «+ Agregar alícuota»). */
export function alicuotaLibre(filas: AlicuotaIva[]): number | null {
  return ALICUOTAS_IVA.find((a) => !filas.some((f) => f.alicuota === a)) ?? null;
}

/**
 * El IVA que dan los ítems en cada alícuota (lo mismo que `iva_de_items` en
 * a_excel.py). Si el detalle suma el neto, los importes son sin IVA; si suma el
 * total (tiques, factura B), lo incluyen.
 */
export function ivaDeItems(items: ItemLeido[], totales: Record<string, number | null>): Map<number, number> {
  const suma = items.reduce((s, x) => s + (x.importe ?? 0), 0);
  const neto = totales['neto_gravado'] ?? null;
  const total = totales['total'] ?? null;
  const conIva = total !== null && (neto === null || Math.abs(suma - total) < Math.abs(suma - neto));
  const salida = new Map<number, number>();
  for (const x of items) {
    if (x.alicuota === null || x.importe === null) continue;
    const iva = conIva ? x.importe - x.importe / (1 + x.alicuota / 100) : (x.importe * x.alicuota) / 100;
    salida.set(x.alicuota, (salida.get(x.alicuota) ?? 0) + iva);
  }
  for (const [a, v] of salida) salida.set(a, redondear(v));
  return salida;
}

export interface FilaDesglose extends AlicuotaIva {
  /** base × alícuota. */
  calculado: number | null;
  /** El IVA de los ítems de esa alícuota (null si el detalle no alcanza para calcularlo). */
  deItems: number | null;
  estado: NivelVerificacion;
  motivo: string;
}

/** Cada fila del desglose con lo que dan las cuentas, al instante (sin esperar al backend). */
export function revisarDesglose(lectura: LecturaExcel): FilaDesglose[] {
  const items = lectura.items;
  const calculable = items.length > 0 && items.every((x) => x.alicuota !== null && x.importe !== null);
  const porAlicuota = calculable ? ivaDeItems(items, lectura.totales) : null;
  const tolItems = Math.max(TOLERANCIA, 0.01 * items.length);
  return (lectura.iva_alicuotas ?? []).map((f) => {
    const calculado = f.base !== null && f.alicuota !== null ? redondear((f.base * f.alicuota) / 100) : null;
    const deItems = porAlicuota && f.alicuota !== null ? (porAlicuota.get(f.alicuota) ?? 0) : null;
    let estado: NivelVerificacion = 'ok';
    let motivo = 'Da con la base y con los ítems.';
    if (f.alicuota === null || f.importe === null) {
      estado = 'aviso';
      motivo = f.alicuota === null ? 'Falta la alícuota.' : 'Falta el importe del IVA.';
    } else if (calculado !== null && Math.abs(calculado - f.importe) > Math.max(TOLERANCIA, Math.abs(f.importe) * 0.0005)) {
      estado = 'error';
      motivo = `El ${String(f.alicuota).replace('.', ',')}% de la base es ${aTexto(calculado)}.`;
    } else if (deItems !== null && Math.abs(deItems - f.importe) > tolItems) {
      estado = 'aviso';
      motivo = `Los ítems de esta alícuota dan ${aTexto(deItems)} de IVA.`;
    } else if (deItems === null) {
      motivo = 'Da con la base (los ítems no alcanzan para comparar).';
    }
    return { ...f, calculado, deItems, estado, motivo };
  });
}

function redondear(n: number): number {
  return Math.round(n * 100) / 100;
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

/** Los productos/servicios cargados en Xubio (los mismos para compras y ventas). */
export const PRODUCTOS_XUBIO: string[] = [
  'Campañas Publicitarias',
  'Campañas Publicitarias 10.5%',
  'COMBUSTIBLE',
  'Combustible al 27%',
  'Combustible exento',
  'Equipos de Computación',
  'Equipos de Computacion 10,5',
  'GASTOS BANCARIOS',
  'GASTOS BANCARIOS 10.5',
  'Gastos de asesoramiento',
  'Gastos de capacitación',
  'Gastos de certificaciones y legalizaciones',
  'Gastos de certificaciones y legalizaciones 21%',
  'Gastos de Libreria',
  'Gastos de limpieza, libreria y refrigerio',
  'Gastos de Mantenimiento',
  'Gastos de movilidad y viáticos',
  'Gastos de Organización',
  'Gastos de Seguridad',
  'GASTOS DE SEGUROS',
  'Gastos de sistemas',
  'Gastos de sistemas (Exento)',
  'GASTOS VARIOS',
  'Gastos Varios al 10,5%',
  'Gastos varios de admnistracion exento',
  'Herramientas',
  'HONORARIOS PROFESIONALES',
  'Honorarios profesionales (exento)',
  'IMPUESTO A LOS CR BANCARIOS',
  'IMPUESTO A LOS DB BANCARIOS',
  'Impuesto país',
  'Instalaciones',
  'INSTALACIONES 10,50%',
  'Intereses financieros',
  'Luz, Gas, Telefono e internet 21%',
  'Luz, Gas, Telefono e internet 27%',
  'Luz, Gas, Telefono e internet exento',
  'Mejoras en inmuebles de terceros',
  'Muebles y Utiles',
  'Muebles y Utiles 10,5%',
  'Muebles y útiles exento',
  'Pagina web, redes, facebook, Instagram, etc',
  'Préstamo BBVA',
  'Producto al 10.5%',
  'Producto al 21% (PROD21)',
  'Producto Exento (Compra de Servicios)',
  'Publicidad',
  'ROPA DE TRABAJO',
  'Servicio de consultoria y procesamiento de datos',
  'Servidores - Emails',
  'SOFTWARE',
  'Software exento',
];

/** Los de siempre, según la operación, para arrancar. */
export const PRODUCTOS_BASE: Record<Operacion, string[]> = { Compras: PRODUCTOS_XUBIO, Ventas: PRODUCTOS_XUBIO };

function leerProductos(): Record<string, string[]> {
  try {
    const crudo = JSON.parse(localStorage.getItem(CLAVE_PRODUCTOS) ?? '{}');
    return crudo && typeof crudo === 'object' ? crudo : {};
  } catch {
    return {};
  }
}

/** Primero los que ya usó esa empresa (el último arriba), después todos los de Xubio. */
export function productosSugeridos(empresa: string, operacion: Operacion): string[] {
  const usados = empresa ? (leerProductos()[`${empresa}|${operacion}`] ?? []) : [];
  const clave = (x: string) => x.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim();
  const vistos = new Set<string>();
  return [...usados, ...PRODUCTOS_BASE[operacion]].filter((x) => !vistos.has(clave(x)) && !!vistos.add(clave(x)));
}

/** Lo que el usuario eligió para una descripción: la próxima vez se sugiere eso. */
// v2: la anterior aprendía de los botones de arriba (p. ej. un puré como «Alquiler»).
const CLAVE_POR_ITEM = 'cf-producto-por-item-v2';
const claveItem = (empresa: string, descripcion: string) =>
  `${empresa}|${descripcion.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[^a-z0-9]+/g, ' ').trim()}`;

export function productoRecordado(empresa: string, descripcion: string): string {
  try {
    return (JSON.parse(localStorage.getItem(CLAVE_POR_ITEM) ?? '{}') as Record<string, string>)[claveItem(empresa, descripcion)] ?? '';
  } catch {
    return '';
  }
}

export function recordarProductoDeItem(empresa: string, descripcion: string, producto: string): void {
  if (!empresa || !descripcion.trim() || !producto) return;
  try {
    const todos = JSON.parse(localStorage.getItem(CLAVE_POR_ITEM) ?? '{}') as Record<string, string>;
    todos[claveItem(empresa, descripcion)] = producto;
    const claves = Object.keys(todos);
    for (const k of claves.slice(0, Math.max(0, claves.length - 500))) delete todos[k];
    localStorage.setItem(CLAVE_POR_ITEM, JSON.stringify(todos));
  } catch {
    /* sin almacenamiento */
  }
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
