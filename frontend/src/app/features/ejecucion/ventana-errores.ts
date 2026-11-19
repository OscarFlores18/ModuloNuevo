import Swal from 'sweetalert2';
import { formatearCuit } from '../../core/cuit.pipe';
import { ResumenOperacion, Advertencia, CambioElegido, CampoCambio, CeldaVista, ComprobanteLetraDistinta, FilaConError, FilaFaltanteExcel } from '../../core/models';
import { claveCambio, claveFila, elegirParaAplicar, estaIncluida, incluir, paraAplicar } from './seleccion';
import { aCorregir, cantidadComprobantes, ordenDe } from './tareas';

/**
 * Ventana de hallazgos al terminar un control: qué encontró la auditoría (y qué
 * pasos fallaron), qué hacer y las filas tal como están en el Excel, con el
 * error remarcado. Solo muestra: no modifica nada.
 *
 * - Arriba, una tira compacta con el resultado de cada operación (Ventas / Compras).
 * - Tres bloques por cliente:
 *   - «Subir a Xubio» (azul, siempre abierto, arriba de todo): partidas de ARCA no
 *     encontradas en el Libro IVA, tal como la hoja «Faltantes en Libro IVA» del AUX,
 *     con un checkbox por fila (qué va a Xubio) y «⬆ Subir a Xubio», que lleva lo
 *     tildado a «Faltantes a Xubio».
 *   - «Error» (rojo, abierto): letra, tipo, importe, CUIT o repetido, en una sola
 *     tabla de pares ARCA/Xubio con la columna «Corrección en Xubio». Cada par se
 *     tilda para corregirlo con el dato de ARCA; «Aplicar cambios» lo hace de verdad
 *     (corrige el Libro IVA en el servidor y vuelve a cruzar).
 *   - «A revisar» (ámbar, abierto): partidas del Libro IVA no encontradas en ARCA,
 *     tal como el bloque al pie de la primera hoja del AUX.
 *   Del Libro IVA, lo que el control ya explicó (tiques, extractos, exterior) lleva
 *   una observación; de los dos lados, lo que también está en «Error» lo avisa.
 * - Filtros: Todos | Error | Subir a Xubio | A revisar.
 * - Cada comprobante se tilda como cargado / resuelto (solo Acceso Total; queda en
 *   este navegador). Comprobante y CUIT se copian con un clic.
 * - Lo grave (el control no sirve) va aparte, arriba de los bloques.
 * - Se cuentan comprobantes (pares ARCA/Xubio), nunca filas.
 * - La grilla entra en el ancho de la ventana: sin scroll horizontal.
 * - Los botones de resolución solo responden en la vista Acceso Total (`canEdit`).
 */

/** Primero lo grave, después lo mal cargado, después lo que falta y al final lo informativo. */
const ORDEN_CLASE: Record<string, number> = { grave: 0, mal: 1, aviso: 2, info: 3 };

/**
 * El aviso prioritario del estudio: «Partidas de ARCA no encontradas en el Libro IVA»
 * (no al revés). Va en «Subir a Xubio», el primer bloque, siempre abierto.
 */
export function esPrioridad(e: Advertencia): boolean {
  return e.tipo === 'faltante_en_xubio';
}

/**
 * Las «Partidas de ARCA no encontradas en el Libro IVA» siempre arriba (aunque sean
 * informativas); después, de lo más grave a lo informativo.
 */
export function ordenarHallazgos(errores: readonly Advertencia[]): Advertencia[] {
  const prioridad = (e: Advertencia) => (e.tipo === 'faltante_en_xubio' ? 0 : 1);
  return [...errores].sort((a, b) => prioridad(a) - prioridad(b) || ORDEN_CLASE[claseDe(a)] - ORDEN_CLASE[claseDe(b)]);
}

/**
 * Qué es cada hallazgo. Un comprobante que falta en un lado NO es un error de
 * carga: es un aviso (ámbar) — falta subirlo, o hay que preguntar. Error (rojo)
 * es lo que quedó mal cargado en Xubio: letra, tipo, CUIT, importe, repetido.
 */
type Clase = 'grave' | 'mal' | 'aviso' | 'info';

const AVISOS: ReadonlySet<Advertencia['tipo']> = new Set(['faltante_en_xubio', 'faltante_en_arca']);

function claseDe(e: Advertencia): Clase {
  if (e.gravedad === 'grave') return 'grave';
  if (e.gravedad === 'info') return 'info';
  return AVISOS.has(e.tipo) ? 'aviso' : 'mal';
}

function esFaltante(e: Advertencia): boolean {
  return AVISOS.has(e.tipo);
}

/** Columnas comunes a ARCA y Xubio (cada planilla las llama distinto). */
const COLUMNAS = ['Fecha', 'Tipo', 'Comprobante', 'CUIT', 'Razón social', 'Total'] as const;
type Columna = (typeof COLUMNAS)[number];
/** Además de las columnas que se ven: el total de ARCA ya en pesos (facturas en otra moneda). */
type Dato = Columna | 'TotalPesos';
const ALIAS: Record<string, Dato> = {
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
  'Total en pesos': 'TotalPesos',
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

/**
 * Los importes con el mismo formato en ARCA y en Xubio (604.887,00). ARCA a veces
 * los manda como texto sin separadores («604887» o «604887,5»).
 */
function importe(v: CeldaVista['valor'] | undefined): string {
  if (typeof v === 'string' && /^-?\d+([.,]\d+)?$/.test(v.trim())) return texto(Number(v.trim().replace(',', '.')));
  return texto(v);
}

/** La CUIT siempre como XX-XXXXXXXX-X (ARCA la manda sin guiones y Xubio con). */
function cuit(v: CeldaVista['valor'] | undefined): string {
  if (v === null || v === undefined) return '';
  return formatearCuit(typeof v === 'number' ? String(Math.trunc(v)) : String(v));
}

const ABREVIATURAS: [RegExp, string][] = [
  [/^factura\b/i, 'FC'],
  // ARCA: «Nota de Crédito A»; Xubio: «Nota Crédito».
  [/^nota (de )?cr[eé]dito\b/i, 'NC'],
  [/^nota (de )?d[eé]bito\b/i, 'ND'],
  // ARCA lo manda como «Liquidaciónes A».
  [/^liquidaci[oó]n(es)?\b/i, 'Liq.'],
  [/^recibo\b/i, 'Rec.'],
  [/^tique factura\b/i, 'TF'],
  [/^otro comprobante\b/i, 'Otro'],
  [/^extracto bancario\b/i, 'Extracto'],
];

/**
 * «1 - Factura A» → «FC A», «3 - Nota de Crédito A» → «NC A», «63 - Liquidación A» → «Liq. A».
 * Lo que no reconoce queda como vino (sin el código del principio). El nombre completo va en el tooltip.
 */
export function abreviarTipo(tipo: string): string {
  const sinCodigo = tipo.replace(/^\s*\d+\s*-\s*/, '').trim();
  for (const [patron, corto] of ABREVIATURAS) {
    if (patron.test(sinCodigo)) return sinCodigo.replace(patron, corto).replace(/\s+/g, ' ');
  }
  return sinCodigo;
}

function normalizar(fila: CeldaVista[]): Partial<Record<Dato, CeldaVista['valor']>> {
  const salida: Partial<Record<Dato, CeldaVista['valor']>> = {};
  for (const c of fila) {
    const col = ALIAS[c.columna];
    if (col && salida[col] === undefined) salida[col] = c.valor;
  }
  return salida;
}

function aNumero(v: CeldaVista['valor'] | undefined): number | null {
  if (typeof v === 'number') return v;
  if (typeof v !== 'string') return null;
  const limpio = v.trim().replace(/\$|\s/g, '');
  // «604.887,50» (es-AR) o «604887.5» / «604887,5».
  const n = Number(/,\d{1,2}$/.test(limpio) ? limpio.replace(/\./g, '').replace(',', '.') : limpio.replace(/,/g, ''));
  return Number.isFinite(n) ? n : null;
}

function pesos(n: number): string {
  return `${n < 0 ? '-' : ''}$${Math.abs(n).toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function plural(n: number, uno: string, varios: string): string {
  return `${n} ${n === 1 ? uno : varios}`;
}

/** Una partida de ARCA que hay que cargar en Xubio, lista para mostrar. */
export interface FilaFaltante {
  /** «CUIT|comprobante»: para el checkbox de «Subir a Xubio». */
  clave: string;
  fecha: string;
  tipo: string;
  tipoCompleto: string;
  comprobante: string;
  cuit: string;
  razon: string;
  total: string;
  /** Por qué no hace falta cargarla, o que ya figura en «Error» (solo con la hoja del Excel). */
  nota?: string;
}

// --- La hoja «Faltantes en Libro IVA» del Excel ---------------------------------------
//
// Lo que se muestra en «Subir a Xubio» es la hoja tal como está en el AUX (es lo mismo
// que sube el botón), sin etiquetas: cada fila lleva un checkbox para elegir a mano si
// va (una factura y su NC del mismo período, gastos no computables…). Solo se avisa si
// la misma partida figura en «Error» (letra/tipo/CUIT distinto).

/** Las hojas «Faltantes en Libro IVA» de los AUX, por «cliente|operación». */
export type FaltantesDelExcel = ReadonlyMap<string, readonly FilaFaltanteExcel[]>;

/** Lo que no cruzó, de cada lado, tal como está en los AUX. */
export interface HojasExcel {
  /** Partidas de ARCA no encontradas en el Libro IVA (hoja «Faltantes en Libro IVA»). */
  sinLibro: FaltantesDelExcel;
  /** Partidas del Libro IVA no encontradas en ARCA (bloque al pie de la primera hoja). */
  sinArca: FaltantesDelExcel;
}

export function claveExcel(cliente: string, operacion: string): string {
  return `${cliente}|${operacion}`;
}

/** Solo las hojas de ese cliente. */
function excelDelCliente(excel: FaltantesDelExcel | undefined, cliente: string): FaltantesDelExcel {
  return new Map([...(excel ?? new Map<string, readonly FilaFaltanteExcel[]>())].filter(([k]) => k.startsWith(`${cliente}|`)));
}

const claveComprobante = claveFila;

const NOTA_EN_ERROR: Record<string, string> = {
  letra_distinta: 'Ver «Error»: letra distinta',
  tipo_distinto: 'Ver «Error»: tipo distinto',
  importe_distinto: 'Ver «Error»: importe distinto',
  cuit_distinto: 'Ver «Error»: CUIT distinto',
};

/**
 * Las observaciones de cada partida (por CUIT y comprobante) según lo que encontró el
 * control. Lado ARCA: solo lo que pasó a «Error» (qué se sube lo decide quien sube).
 * Lado Libro IVA: tiques / extractos, exterior y el lado Xubio de lo que pasó a «Error».
 */
function notasDe(errores: readonly Advertencia[], cliente: string, operacion: string, lado: 'arca' | 'xubio'): Map<string, NonNullable<FilaVista['nota']>> {
  const notas = new Map<string, NonNullable<FilaVista['nota']>>();
  const anotar = (fila: CeldaVista[], nota: NonNullable<FilaVista['nota']>) => {
    const d = normalizar(fila);
    notas.set(claveComprobante(d['CUIT'], d['Comprobante']), nota);
  };
  const faltante = lado === 'arca' ? 'faltante_en_xubio' : 'faltante_en_arca';
  for (const e of errores) {
    if (e.cliente !== cliente || e.operacion !== operacion) continue;
    if (e.tipo === faltante) {
      if (lado === 'arca') continue;
      for (const f of e.comprobantes as FilaConError[]) {
        const nota = f.motivo && NOTA_MOTIVO[f.motivo];
        if (nota) anotar(f.fila, nota);
      }
    } else if (NOTA_EN_ERROR[e.tipo]) {
      const nota = { texto: NOTA_EN_ERROR[e.tipo], tono: 'azul' as const };
      if (e.tipo === 'letra_distinta') {
        for (const c of e.comprobantes as ComprobanteLetraDistinta[]) anotar(lado === 'arca' ? c.fila_arca : c.fila_xubio, nota);
      } else {
        for (const f of e.comprobantes as FilaConError[]) if (f.origen === lado) anotar(f.fila, nota);
      }
    }
  }
  return notas;
}

function datosDeExcel(f: FilaFaltanteExcel): FilaVista['datos'] {
  return { Fecha: f.fecha, Tipo: f.tipo, Comprobante: f.comprobante, CUIT: f.cuit, 'Razón social': f.razon, Total: f.total };
}

/** Las filas de la hoja, listas para la tarjeta de «Qué hay que hacer» (con su observación). */
export function filasDelExcel(filas: readonly FilaFaltanteExcel[], errores: readonly Advertencia[], cliente: string, operacion: string): FilaFaltante[] {
  const notas = notasDe(errores, cliente, operacion, 'arca');
  return filas.map((f) => ({
    clave: claveFila(f.cuit, f.comprobante),
    fecha: f.fecha,
    tipo: abreviarTipo(f.tipo),
    tipoCompleto: f.tipo,
    comprobante: f.comprobante,
    cuit: cuit(f.cuit),
    razon: f.razon,
    total: importe(f.total),
    nota: notas.get(claveComprobante(f.cuit, f.comprobante))?.texto,
  }));
}

/** Un hallazgo «faltante_en_xubio» armado desde la hoja, cuando el control no lo informó (todas fueron a «Error»). */
export function faltanteDesdeExcel(cliente: string, operacion: 'Ventas' | 'Compras', n: number): Advertencia {
  return {
    tipo: 'faltante_en_xubio',
    gravedad: 'corregir',
    cliente,
    operacion,
    titulo: `Partidas de ARCA no encontradas en el Libro IVA ${operacion}`,
    detalle: '',
    acciones: [],
    total: n,
    a_corregir: n,
    comprobantes: [],
    hoja: `Faltantes en Libro IVA ${operacion}`,
  };
}

/** Las partidas de ARCA no encontradas en el Libro IVA del hallazgo (sin la hoja del Excel): todas. */
export function faltantesParaCargar(a: Advertencia): FilaFaltante[] {
  if (a.tipo !== 'faltante_en_xubio') return [];
  return (a.comprobantes as FilaConError[])
    .map((f) => {
      const d = normalizar(f.fila);
      const tipo = texto(d['Tipo']);
      return {
        clave: claveFila(d['CUIT'], d['Comprobante']),
        fecha: texto(d['Fecha']),
        tipo: abreviarTipo(tipo),
        tipoCompleto: tipo,
        comprobante: texto(d['Comprobante']),
        cuit: cuit(d['CUIT']),
        razon: texto(d['Razón social']),
        total: importe(d['Total']),
      };
    });
}

// --- La grilla -----------------------------------------------------------------------

interface FilaVista {
  origen: 'ARCA' | 'Xubio';
  /** Ventas / Compras: se muestra solo si la tabla mezcla las dos. */
  op?: string;
  datos: Partial<Record<Dato, CeldaVista['valor']>>;
  /** HTML ya armado para la celda Comprobante (con la letra marcada). */
  comprobante?: string;
  marca?: 'error' | 'ok' | 'aviso';
  /** En qué columna está la diferencia (por defecto, el comprobante). */
  marcaEn?: 'Comprobante' | 'Tipo' | 'CUIT' | 'Total';
  /** Por qué no hay que hacer nada con esta fila (columna «Observación»). */
  nota?: { texto: string; tono: 'neutro' | 'azul' };
}

/** Un comprobante: su par ARCA/Xubio (o sus copias repetidas) y qué cambiar en Xubio. */
interface Grupo {
  clase?: ClaseError;
  filas: FilaVista[];
  /** HTML de la celda «Corrección en Xubio». */
  correccion?: string;
  /** Para recordar si ya se cargó / resolvió (checkbox de la primera columna). */
  clave?: string;
  /** «Subir a Xubio»: el checkbox de la primera columna elige si la fila va al .zip. */
  subir?: { op: string; fila: string; incluida: boolean };
  /** «Error»: tildado para corregir con el dato de ARCA («Aplicar cambios»). */
  aplicar?: { clave: string; tildado: boolean };
}

/** Qué filas ya se marcaron como cargadas / resueltas, y si se pueden tildar (Acceso Total). */
interface Marcado {
  canEdit: boolean;
  hechas: ReadonlySet<string>;
  /** Para la selección de qué se sube y qué se corrige. */
  ejecucionId?: string;
  cliente: string;
  /** Hay «Aplicar cambios» (control terminado, desde Ejecuciones). */
  conAplicar: boolean;
}

const PREFIJO_HECHAS = 'cf-hallazgos-hechos:';

/** Las filas tildadas quedan en este navegador, por control (sin control, duran lo que la ventana). */
function leerHechas(ejecucionId?: string): Set<string> {
  if (!ejecucionId) return new Set();
  try {
    return new Set(JSON.parse(localStorage.getItem(PREFIJO_HECHAS + ejecucionId) ?? '[]') as string[]);
  } catch {
    return new Set();
  }
}

function guardarHechas(ejecucionId: string | undefined, hechas: ReadonlySet<string>): void {
  if (!ejecucionId) return;
  try {
    localStorage.setItem(PREFIJO_HECHAS + ejecucionId, JSON.stringify([...hechas]));
  } catch {
    // Sin localStorage los tildes duran hasta cerrar la ventana.
  }
}

/** Clave estable de un comprobante dentro de un control (para el tilde de «hecho»). */
function claveDe(e: Advertencia, bloque: string, datos: FilaVista['datos']): string {
  return [e.cliente, e.operacion ?? '', bloque, cuit(datos['CUIT']), texto(datos['Comprobante']), abreviarTipo(texto(datos['Tipo']))].join('|');
}

/** Lado Libro IVA: lo que es normal que no esté en ARCA (la columna ya se llama «Observación»). */
const NOTA_MOTIVO: Partial<Record<NonNullable<FilaConError['motivo']>, NonNullable<FilaVista['nota']>>> = {
  exterior: { texto: 'Del exterior', tono: 'neutro' },
  tique: { texto: 'Tique / extracto', tono: 'neutro' },
};

/**
 * Grilla de ancho fijo (sin scroll horizontal). Cada grupo es un `<tbody>`: los
 * pares ARCA/Xubio quedan juntos y separados del siguiente por un borde. Las
 * columnas Origen, Op. y Observación solo aparecen si hacen falta.
 */
function grilla(grupos: Grupo[], marcado?: Marcado): string {
  const filas = grupos.flatMap((g) => g.filas);
  if (!filas.length) return '';
  const conOrigen = new Set(filas.map((f) => f.origen)).size > 1;
  const conOp = new Set(filas.map((f) => f.op).filter(Boolean)).size > 1;
  const conNota = filas.some((f) => f.nota);
  const conCorr = grupos.some((g) => g.correccion);
  const conSubir = !!marcado && grupos.some((g) => g.subir);
  const conAplicar = !!marcado && marcado.conAplicar && grupos.some((g) => g.aplicar);
  const conHecho = !!marcado && !conSubir && !conAplicar && grupos.some((g) => g.clave);
  const conTilde = conHecho || conSubir || conAplicar;
  const bloqueo = marcado && !marcado.canEdit ? ` disabled title="${BLOQUEADO}"` : '';
  const todas = grupos.every((g) => !g.subir || g.subir.incluida);
  const todosAplicar = grupos.some((g) => g.aplicar) && grupos.every((g) => !g.aplicar || g.aplicar.tildado);
  const cols = [
    conTilde && '<col class="c-hecho">',
    conOrigen && '<col class="c-origen">',
    conOp && '<col class="c-op">',
    '<col class="c-fecha">',
    '<col class="c-tipo">',
    '<col class="c-comp">',
    '<col class="c-cuit">',
    '<col>',
    conNota && '<col class="c-nota">',
    '<col class="c-total">',
    // El importe (con la diferencia) necesita más lugar que letra o tipo.
    conCorr && `<col class="c-corr${grupos.some((g) => g.clase === 'importe') ? ' c-corr-ancha' : ''}">`,
  ]
    .filter(Boolean)
    .join('');
  const cab = [
    conHecho && `<th class="h-hecho" title="${marcado!.canEdit ? 'Marcá lo que ya cargaste o resolviste' : 'Marcado por quien tiene Acceso Total'}">✓</th>`,
    conSubir &&
      `<th class="h-hecho"><input type="checkbox" data-incluir-todas aria-label="Subir todas" title="Tildadas: van a Xubio"${todas ? ' checked' : ''}${bloqueo}></th>`,
    conAplicar &&
      `<th class="h-hecho"><input type="checkbox" data-aplicar-todos aria-label="Tildar todos para aplicar" title="Tildados: se corrigen con el dato de ARCA al tocar «Aplicar cambios»"${todosAplicar ? ' checked' : ''}${bloqueo}></th>`,
    conOrigen && '<th>Origen</th>',
    conOp && '<th>Op.</th>',
    '<th>Fecha</th>',
    '<th>Tipo</th>',
    '<th>Comprobante</th>',
    '<th class="h-cuit">CUIT</th>',
    '<th>Razón social</th>',
    conNota && '<th>Observación</th>',
    '<th class="num">Total</th>',
    conCorr && '<th class="h-corr">Corrección en Xubio</th>',
  ]
    .filter(Boolean)
    .join('');
  const fila = (f: FilaVista, inicio: string, corr: string) => {
    const marcaEn = f.marcaEn ?? 'Comprobante';
    const marca = (col: Columna) => (f.marca && col === marcaEn ? ` marca ${f.marca}` : '');
    const tipo = texto(f.datos['Tipo']);
    const razon = texto(f.datos['Razón social']);
    const nro = texto(f.datos['Comprobante']);
    const comp = f.comprobante ?? esc(nro);
    const cuitTexto = cuit(f.datos['CUIT']);
    // Comprobante y CUIT se copian con un clic (para buscarlos en Xubio).
    const copiar = (valor: string) => (valor ? ` copiable" data-copiar="${esc(valor)}" title="Copiar ${esc(valor)}` : '');
    return `<tr>
      ${inicio}
      ${conOrigen ? `<td class="origen origen-${f.origen.toLowerCase()}">${f.origen}</td>` : ''}
      ${conOp ? `<td class="op">${esc(f.op ?? '')}</td>` : ''}
      <td>${esc(texto(f.datos['Fecha']))}</td>
      <td class="tipo${marca('Tipo')}" title="${esc(tipo)}">${esc(abreviarTipo(tipo))}</td>
      <td class="comp${marca('Comprobante')}${copiar(nro)}">${comp}</td>
      <td class="cuit${marca('CUIT')}${copiar(cuitTexto)}">${esc(cuitTexto)}</td>
      <td class="razon" title="${esc(razon)}">${esc(razon)}</td>
      ${conNota ? `<td>${f.nota ? `<span class="ve-nota ve-nota-${f.nota.tono}">${esc(f.nota.texto)}</span>` : ''}</td>` : ''}
      <td class="num${marca('Total')}">${esc(importe(f.datos['Total']))}</td>
      ${corr}
    </tr>`;
  };
  const cuerpo = grupos
    .map((g) => {
      const hecha = conHecho && !!g.clave && marcado!.hechas.has(g.clave);
      const fuera = conSubir && !!g.subir && !g.subir.incluida;
      const elegido = conAplicar && !!g.aplicar?.tildado;
      const clases = [conCorr && 've-par', hecha && 've-hecha', fuera && 've-excluida', elegido && 've-elegido'].filter(Boolean).join(' ');
      const atributos = `${clases ? ` class="${clases}"` : ''}${g.clase ? ` data-clase="${g.clase}"` : ''}`;
      const tilde = () => {
        if (conAplicar) {
          if (!g.aplicar) return `<td class="hecho" rowspan="${g.filas.length}"></td>`;
          return `<td class="hecho" rowspan="${g.filas.length}"><input type="checkbox" data-aplicar="${esc(g.aplicar.clave)}" aria-label="Corregir con el dato de ARCA" title="Corregir con el dato de ARCA"${g.aplicar.tildado ? ' checked' : ''}${bloqueo}></td>`;
        }
        if (conSubir) {
          if (!g.subir) return `<td class="hecho" rowspan="${g.filas.length}"></td>`;
          const { op, fila, incluida } = g.subir;
          return `<td class="hecho" rowspan="${g.filas.length}"><input type="checkbox" data-incluir data-op="${esc(op)}" data-fila="${esc(fila)}" aria-label="Subir a Xubio"${incluida ? ' checked' : ''}${bloqueo}></td>`;
        }
        if (!g.clave) return `<td class="hecho" rowspan="${g.filas.length}"></td>`;
        return `<td class="hecho" rowspan="${g.filas.length}"><input type="checkbox" data-hecho="${esc(g.clave)}" aria-label="Cargado / resuelto"${hecha ? ' checked' : ''}${bloqueo}></td>`;
      };
      const filasHtml = g.filas
        .map((f, i) =>
          fila(
            f,
            conTilde && i === 0 ? tilde() : '',
            conCorr && i === 0 ? `<td class="corr" rowspan="${g.filas.length}">${g.correccion ?? ''}</td>` : '',
          ),
        )
        .join('');
      return `<tbody${atributos}>${filasHtml}</tbody>`;
    })
    .join('');
  return `<div class="ve-grilla"><table${conCorr ? ' class="con-corr"' : ''}><colgroup>${cols}</colgroup><thead><tr>${cab}</tr></thead>${cuerpo}</table></div>`;
}

function conLetra(comprobante: string, clase: 'error' | 'ok'): string {
  const [letra, ...resto] = comprobante.split('-');
  return `<span class="letra letra-${clase}">${esc(letra)}</span>-${esc(resto.join('-'))}`;
}

/** Las filas tal como vienen (para lo grave y lo que no entra en los tres bloques). */
function filasDe(e: Advertencia): FilaVista[] {
  const clase = claseDe(e);
  return (e.comprobantes as FilaConError[]).map((f) => ({
    origen: f.origen === 'arca' ? 'ARCA' : 'Xubio',
    datos: normalizar(f.fila),
    marca: clase === 'info' || (f.motivo && NOTA_MOTIVO[f.motivo]) ? undefined : clase === 'aviso' ? 'aviso' : 'error',
    nota: f.motivo ? NOTA_MOTIVO[f.motivo] : undefined,
  }));
}

// --- Bloque «Error»: los mal cargados en Xubio, de a pares ----------------------------

type ClaseError = 'letra' | 'tipo' | 'importe' | 'cuit' | 'duplicado';

const CLASE_ERROR: Partial<Record<Advertencia['tipo'], ClaseError>> = {
  letra_distinta: 'letra',
  tipo_distinto: 'tipo',
  importe_distinto: 'importe',
  cuit_distinto: 'cuit',
  duplicado: 'duplicado',
};
const ORDEN_ERROR: readonly ClaseError[] = ['letra', 'tipo', 'importe', 'cuit', 'duplicado'];
const NOMBRE_ERROR: Record<ClaseError, string> = { letra: 'Letra', tipo: 'Tipo', importe: 'Importe', cuit: 'CUIT', duplicado: 'Repetido' };

/** «Letra: A → C», con el valor de Xubio en rojo y el de ARCA en verde. `antes` y `despues` ya escapados. */
function cambio(etiqueta: string, antes: string, despues: string, extra = ''): string {
  return `<span class="ve-corr-et">${etiqueta}:</span> <span class="ve-valor ve-valor-error">${antes}</span> <span class="ve-flecha">→</span> <span class="ve-valor ve-valor-ok">${despues}</span>${extra}`;
}

/** Las filas de un error de a pares, juntadas en (ARCA, Xubio) en el orden en que vienen. */
function paresDe(e: Advertencia): { arca: FilaVista['datos']; xubio: FilaVista['datos'] }[] {
  const filas = e.comprobantes as FilaConError[];
  const arca = filas.filter((f) => f.origen === 'arca');
  const xubio = filas.filter((f) => f.origen === 'xubio');
  return arca.slice(0, xubio.length).map((a, i) => ({ arca: normalizar(a.fila), xubio: normalizar(xubio[i].fila) }));
}

function gruposError(e: Advertencia): Grupo[] {
  const clase = CLASE_ERROR[e.tipo];
  if (!clase) return [];
  const op = e.operacion ?? undefined;
  if (clase === 'letra') {
    return (e.comprobantes as ComprobanteLetraDistinta[]).map((c) => ({
      clase,
      filas: [
        { origen: 'ARCA', op, datos: normalizar(c.fila_arca), comprobante: conLetra(c.comprobante_arca, 'ok'), marca: 'ok' },
        { origen: 'Xubio', op, datos: normalizar(c.fila_xubio), comprobante: conLetra(c.comprobante_xubio, 'error'), marca: 'error' },
      ],
      correccion: cambio('Letra', esc(c.letra_xubio), esc(c.letra_arca)),
      clave: claveDe(e, 'error', normalizar(c.fila_xubio)),
    }));
  }
  if (clase === 'duplicado') {
    // Las copias del mismo comprobante (mismo CUIT, número y tipo) van juntas.
    const porClave = new Map<string, FilaVista[]>();
    for (const f of e.comprobantes as FilaConError[]) {
      const datos = normalizar(f.fila);
      const clave = [cuit(datos['CUIT']), texto(datos['Comprobante']), abreviarTipo(texto(datos['Tipo']))].join('|');
      porClave.set(clave, [...(porClave.get(clave) ?? []), { origen: 'Xubio', op, datos, marca: 'error' }]);
    }
    return [...porClave.values()].map((filas) => {
      const sobran = Math.max(1, filas.length - 1);
      return {
        clase,
        filas,
        correccion: `<span class="ve-corr-et">Repetido:</span> borrar ${sobran === 1 ? 'la copia' : `${sobran} copias`}`,
        clave: claveDe(e, 'error', filas[0].datos),
      };
    });
  }
  const marcaEn: FilaVista['marcaEn'] = clase === 'tipo' ? 'Tipo' : clase === 'cuit' ? 'CUIT' : 'Total';
  return paresDe(e).map(({ arca, xubio }) => {
    let correccion: string;
    if (clase === 'tipo') {
      correccion = cambio('Tipo', esc(abreviarTipo(texto(xubio['Tipo']))), esc(abreviarTipo(texto(arca['Tipo']))));
    } else if (clase === 'cuit') {
      correccion = cambio('CUIT', esc(cuit(xubio['CUIT'])), esc(cuit(arca['CUIT'])));
    } else {
      const nx = aNumero(xubio['Total']);
      // En otra moneda se compara (y se corrige) con el total en pesos.
      const na = aNumero(arca['TotalPesos']) ?? aNumero(arca['Total']);
      const dif = nx !== null && na !== null ? ` <span class="ve-diferencia">(${esc(pesos(Math.abs(na) - Math.abs(nx)))})</span>` : '';
      correccion = cambio(
        'Importe',
        esc(nx === null ? importe(xubio['Total']) : pesos(nx)),
        esc(na === null ? importe(arca['Total']) : pesos(na)),
        dif,
      );
    }
    return {
      clase,
      filas: [
        { origen: 'ARCA', op, datos: arca, marca: 'ok', marcaEn },
        { origen: 'Xubio', op, datos: xubio, marca: 'error', marcaEn },
      ],
      correccion,
      clave: claveDe(e, 'error', xubio),
    };
  });
}

/** El HTML de la corrección como texto («Importe: $1.000,00 → $1.210,00 ($210,00)»). */
function textoPlano(html: string): string {
  return html
    .replace(/<[^>]+>/g, '')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ')
    .trim();
}

/** Un par de «Error» que se puede corregir con el dato de ARCA («Aplicar cambios»). */
export interface CambioPar {
  /** Para la selección (`claveCambio`). */
  clave: string;
  /** Lo que se manda al backend. */
  datos: CambioElegido;
  /** «Importe: $117.149,10 → $116.450,40 (-$698,70)». */
  texto: string;
  comprobanteArca: string;
  razon: string;
}

const NRO_COMPROBANTE = /([A-Za-z])[-\s]*(\d+)[-\s]*(\d+)/;

/** El cambio de un par (letra, tipo, importe o CUIT); null si no se puede armar. */
function cambioDe(e: Advertencia, g: Grupo): CambioPar | null {
  if (!g.clase || g.clase === 'duplicado' || g.filas.length < 2 || !e.operacion) return null;
  const campo: CampoCambio = g.clase;
  const arca = g.filas[0].datos;
  const xubio = g.filas[1].datos;
  const compXubio = texto(xubio['Comprobante']);
  const m = NRO_COMPROBANTE.exec(compXubio);
  if (!m) return null;
  const compArca = texto(arca['Comprobante']);
  const valorArca =
    campo === 'letra' ? compArca.trim().charAt(0).toUpperCase()
    : campo === 'tipo' ? texto(arca['Tipo'])
    : campo === 'importe' ? (aNumero(arca['TotalPesos']) ?? aNumero(arca['Total']) ?? texto(arca['Total']))
    : texto(arca['CUIT']).replace(/\D/g, '');
  if (valorArca === '') return null;
  return {
    clave: claveCambio(e.cliente, e.operacion, campo, claveFila(arca['CUIT'], compArca)),
    datos: {
      cliente: e.cliente,
      operacion: e.operacion === 'Ventas' ? 'Ventas' : 'Compras',
      campo,
      cuit: texto(xubio['CUIT']).replace(/\D/g, ''),
      pv: Number(m[2]),
      numero: Number(m[3]),
      comprobante: compXubio,
      valorArca,
    },
    texto: textoPlano(g.correccion ?? ''),
    comprobanteArca: compArca,
    razon: texto(arca['Razón social']),
  };
}

/** Los pares de un error, cada uno con su tilde de «Aplicar cambios». */
function gruposConCambio(e: Advertencia, ejecucionId: string | undefined): Grupo[] {
  return gruposError(e).map((g) => {
    const c = cambioDe(e, g);
    return c ? { ...g, aplicar: { clave: c.clave, tildado: paraAplicar(ejecucionId, c.clave) } } : g;
  });
}

/** Lo que se puede corregir con «Aplicar cambios» (letra, tipo, importe y CUIT distintos). */
export function cambiosDisponibles(errores: readonly Advertencia[]): CambioPar[] {
  return errores
    .filter((e) => CLASE_ERROR[e.tipo])
    .flatMap((e) => gruposError(e).map((g) => cambioDe(e, g)))
    .filter((c): c is CambioPar => !!c);
}

/** Cuántos comprobantes (no filas) tiene un error de carga. */
function comprobantesConError(e: Advertencia): number {
  return e.tipo === 'duplicado' ? gruposError(e).length : cantidadComprobantes(e);
}

// --- Conteo de los bloques -----------------------------------------------------------

export interface ConteoBloques {
  /** Comprobantes mal cargados en Xubio (pares). */
  error: number;
  /** Partidas de ARCA no encontradas en el Libro IVA (todas, como el Excel). */
  soloArca: number;
  /** Partidas del Libro IVA no encontradas en ARCA (todas, como el Excel). */
  soloXubio: number;
  /** De esas, las que el control ya explicó (tiques, extractos, exterior o ya en «Error»). */
  explicadas: number;
}

/**
 * Cuenta comprobantes, no filas. Con las hojas del Excel (ya filtradas a lo que muestra
 * la ventana), «Subir a Xubio» y «A revisar» cuentan las filas de la hoja; sin ellas,
 * todos los comprobantes del hallazgo.
 */
export function contarBloques(errores: readonly Advertencia[], sinLibro?: FaltantesDelExcel, sinArca?: FaltantesDelExcel): ConteoBloques {
  const n: ConteoBloques = { error: 0, soloArca: 0, soloXubio: 0, explicadas: 0 };
  const deHojas = (hojas: FaltantesDelExcel | undefined, lado: 'arca' | 'xubio') => {
    for (const [clave, filas] of hojas ?? []) {
      const corte = clave.lastIndexOf('|');
      const notas = notasDe(errores, clave.slice(0, corte), clave.slice(corte + 1), lado);
      if (lado === 'arca') n.soloArca += filas.length;
      else n.soloXubio += filas.length;
      n.explicadas += filas.filter((f) => notas.has(claveComprobante(f.cuit, f.comprobante))).length;
    }
  };
  deHojas(sinLibro, 'arca');
  deHojas(sinArca, 'xubio');
  for (const e of errores) {
    if (CLASE_ERROR[e.tipo]) {
      n.error += comprobantesConError(e);
      continue;
    }
    if (!esFaltante(e)) continue;
    const hojas = e.tipo === 'faltante_en_xubio' ? sinLibro : sinArca;
    if (hojas?.has(claveExcel(e.cliente, e.operacion ?? ''))) continue; // contada desde la hoja
    if (e.tipo === 'faltante_en_xubio') n.soloArca += e.total;
    else n.soloXubio += e.total;
    // Lado ARCA: nada se da por explicado (qué se sube lo elige quien sube).
    if (e.tipo === 'faltante_en_arca') n.explicadas += Math.max(0, e.total - aCorregir(e));
  }
  return n;
}

// --- Acciones ------------------------------------------------------------------------

const BLOQUEADO = 'Bloqueado en la vista Empleado';

/** Lo que la ventana necesita para armar el mail de «Consultar al cliente». */
export interface ContextoVentana {
  /** Mail de cada cliente (por nombre), cargado en Clientes. */
  emails: Record<string, string>;
  /** «01/11/2026 al 17/11/2026». */
  periodo: string;
}

/** El de la ventana abierta (se fija al abrirla). */
let contexto: ContextoVentana = { emails: {}, periodo: '' };

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
    if (e.tipo === 'total_no_cierra') {
      const v: Record<string, CeldaVista['valor']> = {};
      for (const c of f.fila) v[c.columna] = c.valor;
      return `- ${texto(d.Fecha)} · ${texto(d.Tipo)} ${texto(d.Comprobante)} · ${texto(d['Razón social'])} (CUIT ${cuit(d.CUIT)}) · total $ ${importe(d.Total)}, columnas $ ${importe(v['Suma columnas'])}, diferencia $ ${importe(v['Diferencia'])}`;
    }
    return `- ${texto(d.Fecha)} · ${texto(d.Tipo)} ${texto(d.Comprobante)} · ${texto(d['Razón social'])} (CUIT ${cuit(d.CUIT)})${total}`;
  });
  const resto = filas.length > MAX_LINEAS_MAIL ? `\n…y ${filas.length - MAX_LINEAS_MAIL} más.` : '';
  const operacion = (e.operacion ?? '').toLowerCase();
  const que =
    e.tipo === 'total_no_cierra'
      ? 'en ARCA el neto, el IVA y los demás conceptos no suman el total (hay una diferencia sin discriminar)'
      : e.tipo === 'faltante_en_xubio'
        ? 'figuran en ARCA y no los tenemos registrados'
        : 'los tenemos registrados y no figuran en ARCA';
  const pregunta =
    e.tipo === 'total_no_cierra'
      ? '¿Nos podés decir a qué corresponde la diferencia (impuestos internos, combustibles, otro concepto)? Si podés, envianos la factura.'
      : '¿Nos podés confirmar si corresponden? Si es así, por favor envianos el comprobante.';
  const asunto = `Consulta de comprobantes de ${operacion} · ${ctx.periodo} · ${e.cliente}`;
  const cuerpo = [
    'Hola,',
    '',
    `Al controlar ${operacion} del período ${ctx.periodo} encontramos ${filas.length === 1 ? 'este comprobante' : 'estos comprobantes'} ${e.tipo === 'total_no_cierra' ? (filas.length === 1 ? 'en el que' : 'en los que') : 'que'} ${que}:`,
    '',
    lineas.join('\n') + resto,
    '',
    pregunta,
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

/** «✉ Consultar al cliente»: abre Gmail con el mail ya armado (o bloqueado en Empleado). */
function botonMail(e: Advertencia, canEdit: boolean): string {
  if (!canEdit) return `<button type="button" class="ve-boton" disabled title="${BLOQUEADO}">✉ Consultar al cliente</button>`;
  const ayuda = contexto.emails[e.cliente]
    ? `Abre Gmail con un mail a ${contexto.emails[e.cliente]} y los comprobantes a revisar.`
    : 'El cliente no tiene email cargado: se abre Gmail sin destinatario (cargalo en Clientes).';
  return `<a class="ve-boton" href="${esc(mailConsulta(e, contexto))}" target="_blank" rel="noopener" title="${esc(ayuda)}">✉ Consultar al cliente</a>`;
}


function destinoDe(accion: string, canEdit: boolean): { href: string } | null {
  const a = accion.toLowerCase();
  if (a.includes('clave de arca') || a.includes('cuit del cliente')) return { href: '/clientes' };
  // Nuevo control no existe para el empleado: «Volver a correr» queda como etiqueta.
  if (a.includes('volver a correr')) return canEdit ? { href: '/control' } : null;
  // «Convertir AUX» es el nombre viejo (controles guardados antes del cambio).
  if (a.includes('faltantes a xubio') || a.includes('convertir aux')) return { href: '/convertir-aux' };
  return null;
}

function botonesYEtiquetas(e: Advertencia, canEdit: boolean): { botones: string; etiquetas: string } {
  const botones: string[] = [];
  const etiquetas: string[] = [];
  for (const accion of e.acciones) {
    if (accion.toLowerCase().includes('xubio')) continue;
    if (accion === 'Sin acción') continue;
    if (accion === 'Consultar al cliente') {
      botones.push(botonMail(e, canEdit));
      continue;
    }
    const destino = destinoDe(accion, canEdit);
    if (destino && canEdit) {
      botones.push(`<a class="ve-boton" href="${esc(destino.href)}">${esc(accion)} →</a>`);
    } else if (destino) {
      botones.push(`<button type="button" class="ve-boton" disabled title="${BLOQUEADO}">${esc(accion)}</button>`);
    } else {
      etiquetas.push(`<span class="ve-etiqueta">${esc(accion)}</span>`);
    }
  }
  return { botones: botones.join(''), etiquetas: etiquetas.join('') };
}

/** «Subir a Xubio» de un clic en las partidas de ARCA no encontradas en el Libro IVA. */
function botonSubir(e: Advertencia, indice: number, canEdit: boolean, conOp: boolean, tildadas = aCorregir(e)): string {
  const texto = `⬆ Subir${conOp && e.operacion ? ` ${e.operacion}` : ''} a Xubio`;
  const op = esc(e.operacion ?? '');
  if (!canEdit) return `<button type="button" class="ve-boton ve-boton-chico" disabled title="${BLOQUEADO}">${esc(texto)}</button>`;
  return `<button type="button" class="ve-boton ve-boton-chico" data-subir="${indice}" data-subir-op="${op}"${tildadas ? '' : ' disabled'} title="Lleva los comprobantes tildados a «Subir a Xubio», listos para cargar">${esc(texto)}</button>`;
}

// --- Los bloques ---------------------------------------------------------------------

function cantidadTexto(n: number): string {
  return `<span class="ve-cantidad"> (${plural(n, 'comprobante', 'comprobantes')})</span>`;
}

function plegar(n: number): string {
  return `<span class="ve-plegar" data-abierto="Ocultar ▴" data-cerrado="${esc(`Ver detalle${n ? ` (${n})` : ''} ▾`)}" aria-hidden="true"></span>`;
}

/** «Compras» si todo el bloque es de una sola operación (si mezcla, va la columna Op.). */
function metaOperacion(items: readonly Advertencia[]): string {
  const ops = new Set(items.map((e) => e.operacion).filter(Boolean));
  return ops.size === 1 ? `<span class="ve-meta">${esc([...ops][0])}</span>` : '';
}

function notaMas(mostrados: number, total: number, hoja?: string | null): string {
  if (total <= mostrados) return '';
  return `<p class="ve-mas">Se muestran ${mostrados} de ${total}${hoja ? ` · el resto en la hoja «${esc(hoja)}» del Excel` : ''}</p>`;
}

/**
 * Lo que no cruzó, de un lado, tal como está en el Excel: una fila por comprobante,
 * con su observación si el control ya la explicó. Sin la hoja (no se pudo leer),
 * todas las del hallazgo con su observación.
 */
function sinCruce(
  items: readonly Advertencia[],
  cliente: string,
  hojas: FaltantesDelExcel,
  lado: 'arca' | 'xubio',
  ejecucionId?: string,
): { ops: ('Ventas' | 'Compras')[]; grupos: Grupo[]; porOp: Map<'Ventas' | 'Compras', { n: number; adv?: Advertencia }>; hoja: string | null } {
  const tipo = lado === 'arca' ? 'faltante_en_xubio' : 'faltante_en_arca';
  const bloque = lado === 'arca' ? 'subir' : 'revisar';
  const advs = items.filter((e) => e.tipo === tipo);
  // Una hoja vacía (esa operación no tiene partidas) no cuenta.
  const opsHoja = [...hojas].filter(([, filas]) => filas.length).map(([k]) => k.slice(cliente.length + 1));
  const ops = (['Ventas', 'Compras'] as const).filter((op) => opsHoja.includes(op) || advs.some((e) => e.operacion === op && e.total));
  const grupos: Grupo[] = [];
  const porOp = new Map<'Ventas' | 'Compras', { n: number; adv?: Advertencia }>();
  let hoja: string | null = null;
  for (const op of ops) {
    const adv = advs.find((e) => e.operacion === op);
    const ref = adv ?? faltanteDesdeExcel(cliente, op, 0);
    const notas = notasDe(items, cliente, op, lado);
    const origen = lado === 'arca' ? 'ARCA' : 'Xubio';
    const fila = (datos: FilaVista['datos']): Grupo => {
      const clave = claveComprobante(datos['CUIT'], datos['Comprobante']);
      const nota = notas.get(clave);
      const filas: FilaVista[] = [{ origen, op, datos, marca: nota || lado === 'arca' ? undefined : 'aviso', nota }];
      // ARCA: el checkbox elige si va a Xubio. Libro IVA: el tilde de «revisado».
      return lado === 'arca'
        ? { filas, subir: { op, fila: clave, incluida: estaIncluida(ejecucionId, cliente, op, clave) } }
        : { filas, clave: claveDe(ref, bloque, datos) };
    };
    const filas = hojas.get(claveExcel(cliente, op));
    if (filas) {
      grupos.push(...filas.map((f) => fila(datosDeExcel(f))));
      porOp.set(op, { n: filas.length, adv });
    } else if (adv) {
      grupos.push(...(adv.comprobantes as FilaConError[]).map((f) => fila(normalizar(f.fila))));
      porOp.set(op, { n: adv.total, adv });
      hoja = adv.hoja;
    }
  }
  return { ops, grupos, porOp, hoja };
}

/** «Libro IVA Compras» si es de una sola operación; si mezcla, «Libro IVA». */
function libroDe(ops: readonly string[]): string {
  return ops.length === 1 ? `Libro IVA ${ops[0]}` : 'Libro IVA';
}

/**
 * «Subir a Xubio»: partidas de ARCA no encontradas en el Libro IVA, tal como están en
 * la hoja «Faltantes en Libro IVA» del Excel (lo mismo que sube el botón). Va arriba
 * de todo, siempre abierto.
 */
function bloqueSubir(
  items: readonly Advertencia[],
  cliente: string,
  hojas: FaltantesDelExcel,
  canEdit: boolean,
  registrar: (a: Advertencia) => number,
  conSubida: boolean,
  marcado: Marcado,
): string {
  const { ops, grupos, porOp, hoja } = sinCruce(items, cliente, hojas, 'arca', marcado.ejecucionId);
  if (!ops.length) return '';
  let n = 0;
  const botones: string[] = [];
  for (const [op, { n: k, adv }] of porOp) {
    n += k;
    const a = adv ?? faltanteDesdeExcel(cliente, op, k);
    const tildadas = grupos.filter((g) => g.subir?.op === op && g.subir.incluida).length;
    if (conSubida) botones.push(botonSubir(a, registrar(a), canEdit, porOp.size > 1, tildadas));
  }
  const tildadas = grupos.filter((g) => g.subir?.incluida).length;
  const meta = `<span class="ve-meta" data-cuenta>${tildadas} de ${grupos.length} para subir</span>${ops.length === 1 ? `<span class="ve-meta">${ops[0]}</span>` : ''}`;
  return `
    <details class="ve-error ve-subir ve-bloque ve-prioridad" data-bloque="subir" open>
      <summary tabindex="-1">
        <span class="ve-insignias"><span class="ve-insignia">Subir a Xubio</span></span>
        <span class="ve-cab">
          <span class="ve-titulo">Partidas de ARCA no encontradas en el ${esc(libroDe(ops))}${cantidadTexto(n)}</span>
          <span class="ve-que-hacer">Cargá estos comprobantes en Xubio. Destildá los que no van (una factura y su NC del mismo período, gastos no computables…).</span>
        </span>
        <span class="ve-derecha">${meta}${botones.length ? `<span class="ve-acciones">${botones.join('')}</span>` : ''}</span>
      </summary>
      <div class="ve-cuerpo">${grilla(grupos, marcado)}${notaMas(grupos.length, n, hoja)}</div>
    </details>`;
}

/** «A revisar»: partidas del Libro IVA no encontradas en ARCA, tal como están en el Excel. */
function bloqueRevisar(items: readonly Advertencia[], cliente: string, hojas: FaltantesDelExcel, marcado: Marcado): string {
  const { ops, grupos, porOp, hoja } = sinCruce(items, cliente, hojas, 'xubio');
  const n = [...porOp.values()].reduce((s, o) => s + o.n, 0);
  if (!n) return '';
  const explicadas = grupos.filter((g) => g.filas[0].nota).length;
  const meta = ops.length === 1 ? `<span class="ve-meta">${ops[0]}</span>` : '';
  // Un mail por operación con las que hay que consultar (sin las normales).
  const mails = [...porOp.values()]
    .map((o) => o.adv)
    .filter((a): a is Advertencia => !!a && (a.comprobantes as FilaConError[]).some((f) => 'fila' in f && !f.motivo))
    .map((a) => botonMail(a, marcado.canEdit))
    .join('');
  const queHacer =
    n > explicadas
      ? 'Consultalas con el cliente: pueden ser facturas manuales (CAI), tiques o errores de tipeo.'
      : 'Ya están explicadas: mirá la observación de cada una.';
  return `
    <details class="ve-error ve-aviso ve-bloque" data-bloque="revisar" open>
      <summary>
        <span class="ve-insignias"><span class="ve-insignia">A revisar</span></span>
        <span class="ve-cab">
          <span class="ve-titulo">Partidas del ${esc(libroDe(ops))} no encontradas en ARCA${cantidadTexto(n)}</span>
          <span class="ve-que-hacer">${queHacer}</span>
        </span>
        <span class="ve-derecha">${meta}${mails}${plegar(n)}</span>
      </summary>
      <div class="ve-cuerpo">${grilla(grupos, marcado)}${notaMas(grupos.length, n, hoja)}</div>
    </details>`;
}

/** 2. «Error»: letra, tipo, importe, CUIT y repetidos en una sola tabla de pares. */
function bloqueError(items: readonly Advertencia[], marcado: Marcado): string {
  const errores = items.filter((e) => CLASE_ERROR[e.tipo]);
  if (!errores.length) return '';
  const grupos = errores
    .flatMap((e) => gruposConCambio(e, marcado.ejecucionId))
    .sort((a, b) => ORDEN_ERROR.indexOf(a.clase!) - ORDEN_ERROR.indexOf(b.clase!));
  const n = contarBloques(errores).error;
  const porClase = ORDEN_ERROR.map((c) => [c, contarBloques(errores.filter((e) => CLASE_ERROR[e.tipo] === c)).error] as const).filter(([, k]) => k > 0);
  const pills =
    porClase.length > 1
      ? `<div class="ve-pills" role="group" aria-label="Filtrar errores">
          <button type="button" class="ve-pill" data-pill="" aria-pressed="true">Todos (${n})</button>
          ${porClase.map(([c, k]) => `<button type="button" class="ve-pill" data-pill="${c}" aria-pressed="false">${NOMBRE_ERROR[c]} (${k})</button>`).join('')}
        </div>`
      : '';
  return `
    <details class="ve-error ve-mal ve-bloque" data-bloque="error" open>
      <summary>
        <span class="ve-insignias"><span class="ve-insignia">Error</span></span>
        <span class="ve-cab">
          <span class="ve-titulo">Comprobantes con diferencias en Xubio${cantidadTexto(n)}</span>
          <span class="ve-que-hacer">${
            marcado.conAplicar && grupos.some((g) => g.aplicar)
              ? 'Tildá los que querés corregir con el dato de ARCA y tocá «Aplicar cambios»: se corrige el Libro IVA, se vuelve a cruzar y queda un Excel nuevo.'
              : 'Corregilos en Xubio con el dato de ARCA.'
          }</span>
        </span>
        <span class="ve-derecha">${metaOperacion(errores)}${plegar(n)}</span>
      </summary>
      <div class="ve-cuerpo">
        ${pills}
        ${grilla(grupos, marcado)}${notaMas(grupos.length, n, 'Libro Iva')}
        ${
          marcado.conAplicar && grupos.some((g) => g.aplicar)
            ? `<div class="ve-aplicar"><span class="ve-aplicar-estado" data-aplicar-cuenta aria-live="polite"></span><button type="button" class="ve-boton-aplicar" data-aplicar-btn${marcado.canEdit ? '' : ` disabled title="${BLOQUEADO}"`}>Aplicar cambios</button></div>`
            : ''
        }
      </div>
    </details>`;
}

/** Lo que no es un comprobante (ARCA vacío, un paso que falló…): una tarjeta suelta, arriba. */
/**
 * «Las columnas no suman el total»: cada factura de ARCA con la suma de sus
 * columnas, el Imp. Total, la diferencia (lo que ARCA no discrimina) y cómo
 * quedó en Xubio.
 */
function grillaSuma(e: Advertencia): string {
  const filas = (e.comprobantes as FilaConError[]).filter((f) => 'fila' in f);
  if (!filas.length) return '';
  const cab = ['Fecha', 'Comprobante', 'Razón social', 'Suma de columnas', 'Imp. Total', 'Diferencia', 'En Xubio']
    .map((c, i) => `<th${i >= 3 ? ' class="num"' : ''}>${esc(c)}</th>`)
    .join('');
  const cuerpo = filas
    .map((f) => {
      const d = normalizar(f.fila);
      const v: Record<string, CeldaVista['valor']> = {};
      for (const c of f.fila) v[c.columna] = c.valor;
      const enXubio = v['Total Xubio'];
      const bienEnXubio = typeof enXubio === 'number' && typeof d.Total === 'number' && Math.abs(Math.abs(enXubio) - Math.abs(d.Total)) <= 1;
      const xubio = enXubio === null || enXubio === undefined ? '<td class="num">No está</td>' : `<td class="num${bienEnXubio ? ' marca ok' : ' marca error'}">${esc(importe(enXubio))}</td>`;
      return `<tr><td>${esc(texto(d.Fecha))}</td><td class="comp">${esc(texto(d.Comprobante))}</td><td title="${esc(texto(d.Tipo))} · CUIT ${esc(cuit(d.CUIT))}">${esc(texto(d['Razón social']))}</td><td class="num">${esc(importe(v['Suma columnas']))}</td><td class="num">${esc(importe(d.Total))}</td><td class="num marca aviso">${esc(importe(v['Diferencia']))}</td>${xubio}</tr>`;
    })
    .join('');
  return `<div class="ve-grilla"><table><thead><tr>${cab}</tr></thead><tbody>${cuerpo}</tbody></table></div>`;
}

function tarjeta(e: Advertencia, canEdit: boolean): string {
  const clase = e.tipo === 'total_no_cierra' && e.gravedad !== 'info' ? 'aviso' : claseDe(e);
  const n = e.total;
  const { botones, etiquetas } = botonesYEtiquetas(e, canEdit);
  const orden = ordenDe(e);
  return `
    <details class="ve-error ve-${clase}" open>
      <summary>
        <span class="ve-insignias"><span class="ve-insignia">${clase === 'grave' ? 'Grave' : clase === 'aviso' ? 'A revisar' : 'Error'}</span></span>
        <span class="ve-cab">
          <span class="ve-titulo">${esc(e.titulo)}${n ? cantidadTexto(n) : ''}</span>
          ${orden ? `<span class="ve-que-hacer">${esc(orden.orden)}</span>` : ''}
          ${etiquetas ? `<span class="ve-etiquetas">${etiquetas}</span>` : ''}
        </span>
        <span class="ve-derecha">${e.operacion && !e.titulo.includes(e.operacion) ? `<span class="ve-meta">${esc(e.operacion)}</span>` : ''}${botones}${plegar(n)}</span>
      </summary>
      <div class="ve-cuerpo">
        ${e.detalle ? `<p class="ve-detalle">${esc(e.detalle)}</p>` : ''}
        ${e.tipo === 'total_no_cierra' ? grillaSuma(e) : grilla([{ filas: filasDe(e) }])}
      </div>
    </details>`;
}

function esSuelta(e: Advertencia): boolean {
  return !CLASE_ERROR[e.tipo] && !esFaltante(e) && e.gravedad !== 'info';
}

const ESTILOS = `
  <style>
    /* El popup es una grilla: título, contenido y pie. El contenido toma el alto que
       queda (minmax(0, 1fr)) y scrollea; «Cerrar» nunca se va de la pantalla. */
    .ve-popup { max-height: 94vh; grid-template-rows: auto minmax(0, 1fr) auto; padding: 0; border-radius: 16px; overflow: hidden; }
    .ve-popup .swal2-title { padding: 22px 72px 16px 28px; font-size: 1.5em; font-weight: 800; text-align: left; color: #0f172a; border-bottom: 1px solid #e2e8f0; }
    .ve-popup .swal2-close { position: absolute; top: 14px; right: 14px; width: 44px; height: 44px; margin: 0; border-radius: 10px; font-size: 2rem; color: #64748b; }
    .ve-popup .swal2-close:hover { background: #f1f5f9; color: #0f172a; }
    .ve-popup .swal2-close:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; box-shadow: none; }
    .ve-popup .swal2-html-container { margin: 0; padding: 16px 28px 20px; min-height: 0; max-height: none; overflow-y: auto; overflow-x: hidden; overscroll-behavior: contain; background: #f4f7fb; }
    .ve-popup .swal2-actions { margin: 0; padding: 14px 28px; width: 100%; justify-content: flex-end; border-top: 1px solid #e2e8f0; background: #fff; box-sizing: border-box; }
    .ve-popup .swal2-confirm { min-height: 44px; min-width: 140px; margin: 0; padding: 10px 28px; border-radius: 10px; font-size: 1rem; font-weight: 700; }
    .ve { text-align: left; color: #1f2a37; font-size: 14px; }

    /* Debajo del título: cliente y período. */
    .ve-sub { margin: 0 0 14px; color: #64748b; font-size: 14.5px; font-weight: 600; }

    /* Solo si hay errores graves: el resultado no es confiable. */
    .ve-grave-aviso { display: flex; align-items: center; gap: 12px; margin: 0 0 14px; padding: 12px 16px; border: 1px solid #fecaca; border-radius: 12px; background: #fef2f2; color: #7f1d1d; font-size: 14.5px; line-height: 1.4; }
    .ve-grave-icono { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 999px; background: #dc2626; color: #fff; font-weight: 900; }

    /* Resumen: una tarjeta por operación, con cuánto coincide y una barra. */
    .ve-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 280px), 1fr)); gap: 12px; margin: 0 0 18px; padding: 0; list-style: none; }
    .ve-kpi { display: flex; flex-direction: column; gap: 8px; padding: 14px 16px; border: 1px solid #e2e8f0; border-radius: 12px; background: #fff; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05); }
    .ve-kpi-cab { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .ve-kpi-op { font-size: 13px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; color: #475569; }
    .ve-kpi-cliente { font-weight: 700; color: #334155; text-transform: none; letter-spacing: 0; }
    .ve-kpi-num { font-size: 14px; color: #475569; }
    .ve-kpi-num b { margin-right: 2px; font-size: 24px; color: #0f172a; font-variant-numeric: tabular-nums; }
    .ve-kpi-barra { display: block; height: 8px; border-radius: 999px; background: #e2e8f0; overflow: hidden; }
    .ve-kpi-barra > span { display: block; height: 100%; border-radius: inherit; background: #16a34a; }
    .ve-kpi-mal .ve-kpi-barra > span { background: #f59e0b; }
    .ve-kpi-estado { display: inline-flex; align-items: center; gap: 5px; padding: 3px 12px; border-radius: 999px; font-size: 13px; font-weight: 700; white-space: nowrap; }
    .ve-kpi-ok .ve-kpi-estado { background: #e7f5ec; color: #166534; }
    .ve-kpi-mal { border-color: #fcd9a0; }
    .ve-kpi-mal .ve-kpi-estado { background: #fdf0d9; color: #7a4f0f; }

    /* Filtros (Todos | Error | Subir a Xubio | A revisar): pestañas grandes, una por tipo. */
    /* top negativo = el relleno de arriba del contenedor: sin eso la barra se pega 16 px
       más abajo del borde y por ese hueco se ve pasar la lista. */
    .ve-barra { position: sticky; top: -16px; z-index: 3; display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; margin: 0 -28px 14px; padding: 10px 28px; background: rgba(244, 247, 251, 0.96); border-bottom: 1px solid #e2e8f0; backdrop-filter: blur(4px); }
    .ve-empresa { margin: 0; font-size: 13px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; color: #334155; }
    .ve-filtros, .ve-pills { display: flex; flex-wrap: wrap; gap: 8px; }
    .ve-pills { margin: 0 0 12px; }
    .ve-chip, .ve-pill { display: inline-flex; align-items: center; gap: 6px; min-height: 40px; border: 1px solid #cbd5e1; background: #fff; color: #334155; font: inherit; font-size: 14px; font-weight: 700; padding: 6px 16px; border-radius: 999px; cursor: pointer; white-space: nowrap; transition: background-color .15s, border-color .15s; }
    .ve-pill { min-height: 36px; font-size: 13.5px; padding: 5px 14px; }
    .ve-chip:hover:not(:disabled), .ve-pill:hover { border-color: #2563eb; background: #eff6ff; }
    .ve-chip:focus-visible, .ve-pill:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
    .ve-chip[aria-pressed='true'], .ve-pill[aria-pressed='true'] { background: #1d4ed8; border-color: #1d4ed8; color: #fff; }
    .ve-chip:disabled { opacity: .45; cursor: not-allowed; }
    .ve-chip-num { display: inline-flex; align-items: center; justify-content: center; min-width: 22px; height: 22px; padding: 0 6px; border-radius: 999px; background: #eef2f7; color: #334155; font-size: 12.5px; font-weight: 800; }
    .ve-chip[aria-pressed='true'] .ve-chip-num { background: rgba(255, 255, 255, 0.22); color: #fff; }

    .ve-grupo + .ve-grupo { margin-top: 24px; }
    .ve-grupo[hidden], .ve-bloque[hidden] { display: none; }
    .ve-cliente { margin: 0 0 10px; font-size: 14px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; color: #334155; }

    /* Tarjetas / bloques: título, qué hacer y acciones a la derecha. */
    .ve-error { background: #fff; border: 1px solid #e2e8f0; border-radius: 12px; margin-bottom: 14px; overflow: hidden; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05); }
    .ve-error summary { cursor: pointer; display: flex; align-items: flex-start; gap: 14px; padding: 16px 18px; list-style: none; }
    .ve-error summary:focus-visible { outline: 2px solid #2563eb; outline-offset: -2px; }
    .ve-error summary::-webkit-details-marker { display: none; }
    .ve-insignias { flex: none; display: flex; flex-direction: column; align-items: flex-start; gap: 4px; margin-top: 2px; }
    .ve-insignia { font-size: 12px; font-weight: 800; padding: 4px 10px; border-radius: 6px; text-transform: uppercase; letter-spacing: .04em; white-space: nowrap; }
    .ve-cab { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 8px; }
    .ve-titulo { font-weight: 800; font-size: 16.5px; line-height: 1.3; color: #0f172a; }
    .ve-cantidad { font-weight: 600; color: #64748b; }
    .ve-que-hacer { display: block; padding: 8px 12px; border-radius: 8px; background: #f1f5ff; color: #1e3a8a; font-size: 14px; font-weight: 600; line-height: 1.45; }
    .ve-que-hacer::before { content: 'Qué hacer'; display: inline-block; margin-right: 8px; padding: 1px 8px; border-radius: 999px; background: #1d4ed8; color: #fff; font-size: 11.5px; font-weight: 800; letter-spacing: .03em; text-transform: uppercase; vertical-align: 1px; }
    .ve-etiquetas { display: flex; flex-wrap: wrap; gap: 6px; }
    .ve-etiqueta { display: inline-flex; align-items: center; font-size: 13px; font-weight: 600; padding: 3px 12px; border-radius: 999px; background: #eef2f7; color: #334155; }
    .ve-sin-accion { display: inline-flex; align-items: center; font-size: 13px; font-weight: 700; padding: 3px 12px; border-radius: 999px; background: #e8eef9; color: #3b5b9a; }
    .ve-derecha { flex: none; display: flex; align-items: center; flex-wrap: wrap; justify-content: flex-end; gap: 10px; max-width: 46%; }
    .ve-meta { font-size: 13px; font-weight: 600; color: #64748b; white-space: nowrap; }
    .ve-acciones { display: inline-flex; flex-wrap: wrap; gap: 8px; }
    .ve-boton { border: 0; cursor: pointer; font-family: inherit; display: inline-flex; align-items: center; justify-content: center; gap: 6px; min-height: 40px; font-size: 14.5px; font-weight: 700; padding: 8px 18px; border-radius: 10px; background: #2563eb; color: #fff !important; text-decoration: none; white-space: nowrap; box-shadow: 0 2px 8px rgba(37, 99, 235, 0.25); }
    .ve-boton:hover:not(:disabled) { background: #1d4ed8; }
    .ve-boton:focus-visible { outline: 2px solid #1d4ed8; outline-offset: 2px; }
    .ve-boton-chico { background: #15803d; box-shadow: 0 2px 8px rgba(21, 128, 61, 0.25); }
    .ve-boton-chico:hover:not(:disabled) { background: #166534; }
    .ve-boton:disabled { background: #cbd5e1; color: #f8fafc !important; cursor: not-allowed; box-shadow: none; }
    .ve-plegar { display: inline-flex; align-items: center; min-height: 36px; padding: 4px 12px; border: 1px solid #dbe3ee; border-radius: 8px; background: #fff; font-size: 13.5px; font-weight: 700; color: #1d4ed8; white-space: nowrap; }
    .ve-error summary:hover .ve-plegar { border-color: #2563eb; background: #eff6ff; }
    .ve-plegar::after { content: attr(data-cerrado); }
    details[open] > summary .ve-plegar::after { content: attr(data-abierto); }
    .ve-cuerpo { padding: 0 18px 18px; }

    /* Jerarquía: color solo en lo que pide acción; lo informativo, neutro. */
    .ve-grave { border-left: 5px solid #c53030; }
    .ve-mal { border-left: 5px solid #dc2626; }
    .ve-aviso { border-left: 5px solid #d97706; }
    .ve-subir { border-left: 5px solid #2563eb; }
    .ve-subir .ve-insignia { background: #dbeafe; color: #1d4ed8; }
    .ve-info { border-left: 5px solid #cbd5e1; background: #fbfcfe; }
    .ve-info .ve-titulo { color: #334155; font-weight: 600; }
    .ve-grave .ve-insignia { background: #c53030; color: #fff; }
    .ve-mal .ve-insignia { background: #fde2e2; color: #9b2c2c; }
    .ve-aviso .ve-insignia { background: #fdf0d9; color: #7a4f0f; }
    .ve-info .ve-insignia { background: #eef2f7; color: #475569; }
    .ve-grave .ve-que-hacer, .ve-mal .ve-que-hacer { background: #fef2f2; color: #7f1d1d; }
    .ve-grave .ve-que-hacer::before, .ve-mal .ve-que-hacer::before { background: #c53030; }
    .ve-aviso .ve-que-hacer { background: #fffbeb; color: #78350f; }
    .ve-aviso .ve-que-hacer::before { background: #b45309; }
    /* Partidas de ARCA que hay que cargar: arriba y siempre abierta (no se pliega). */
    .ve-prioridad > summary { cursor: default; }

    .ve-vacio { margin: 0; padding: 12px 14px; border-radius: 10px; background: #fff; color: #475569; }
    .ve-resumen-info { margin: 0 0 10px; color: #334155; font-weight: 600; }

    .ve-detalle { margin: 0 0 12px; color: #475569; font-size: 14.5px; line-height: 1.55; }
    .ve-mas { margin: 8px 0 0; font-size: 13px; color: #64748b; }

    /* Grilla de ancho fijo: entra en la ventana, la razón social se corta con «…». */
    .ve-grilla { border: 1px solid #e2e8f0; border-radius: 10px; overflow: hidden; background: #fff; }
    .ve-grilla tbody:not(.ve-par) tr:hover td { background: #f8fafc; }
    .ve-grilla table { border-collapse: collapse; width: 100%; table-layout: fixed; font: 13.5px 'Segoe UI', Arial, sans-serif; }
    .ve-grilla col.c-hecho { width: 42px; }
    .ve-grilla th.h-hecho, .ve-grilla td.hecho { text-align: center; padding-left: 0; padding-right: 0; vertical-align: middle; }
    .ve-grilla td.hecho input { width: 18px; height: 18px; margin: 0; cursor: pointer; accent-color: #15803d; }
    .ve-grilla td.hecho input:disabled { cursor: not-allowed; }
    /* Lo ya cargado / resuelto queda apagado (sigue a la vista). */
    .ve-grilla tbody.ve-hecha td:not(.hecho) { opacity: .45; text-decoration: line-through; text-decoration-color: #94a3b8; }
    /* Destildada: no va a Xubio (sigue a la vista, apagada). */
    .ve-grilla tbody.ve-excluida td:not(.hecho) { opacity: .4; }
    .ve-grilla th.h-hecho input { width: 18px; height: 18px; margin: 0; cursor: pointer; accent-color: #2563eb; vertical-align: middle; }
    .ve-subir .ve-grilla td.hecho input { accent-color: #2563eb; }
    /* «Usar dato ARCA» / «Mantener Xubio», debajo de la corrección. */
    .ve-grilla tbody.ve-elegido td { background: #f0fdf4; }
    .ve-mal .ve-grilla td.hecho input { accent-color: #15803d; }
    .ve-aplicar { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px 14px; margin-top: 10px; padding-top: 10px; border-top: 1px solid #fecaca; }
    .ve-aplicar-estado { font-size: 14px; font-weight: 600; color: #475569; }
    .ve-boton-aplicar { min-height: 42px; border: 0; border-radius: 10px; background: #15803d; color: #fff; font: inherit; font-size: 15px; font-weight: 700; padding: 8px 22px; cursor: pointer; box-shadow: 0 2px 8px rgba(21, 128, 61, 0.25); }
    .ve-boton-aplicar:hover:not(:disabled) { background: #166534; }
    .ve-boton-aplicar:disabled { background: #94a3b8; cursor: not-allowed; }
    .ve-grilla td.copiable { cursor: copy; }
    .ve-grilla td.copiable:hover { text-decoration: underline dotted; }
    .ve-grilla td.ve-copiado { background: #dcfce7 !important; }
    .ve-grilla col.c-origen { width: 56px; }
    .ve-grilla col.c-op { width: 68px; }
    .ve-grilla col.c-fecha { width: 94px; }
    .ve-grilla col.c-tipo { width: 70px; }
    .ve-grilla col.c-comp { width: 160px; }
    .ve-grilla col.c-cuit { width: 122px; }
    .ve-grilla col.c-nota { width: 162px; }
    .ve-grilla col.c-total { width: 118px; }
    .ve-grilla col.c-corr { width: 200px; }
    .ve-grilla col.c-corr-ancha { width: 312px; }
    .ve-grilla th { background: #f8fafc; color: #475569; font-size: 12.5px; font-weight: 700; text-align: left; padding: 9px 10px; border-bottom: 1px solid #e2e8f0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .ve-grilla th.num { text-align: right; }
    .ve-grilla th.h-corr { color: #9b2c2c; border-left: 1px solid #e2e8f0; }
    .ve-grilla td { padding: 8px 10px; border-bottom: 1px solid #eef2f7; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .ve-grilla tbody:last-child tr:last-child td { border-bottom: 0; }
    /* Cada par ARCA/Xubio es un mini-bloque: línea fina punteada adentro y
       borde marcado (con aire debajo de Xubio) entre un par y el siguiente. */
    .ve-grilla tbody.ve-par td { border-bottom: 0; }
    .ve-grilla tbody.ve-par tr + tr td { border-top: 1px dashed #e2e8f0; }
    .ve-grilla tbody.ve-par tr:last-child td { padding-bottom: 9px; }
    .ve-grilla tbody.ve-par tr:first-child td { padding-top: 7px; }
    .ve-grilla tbody.ve-par + tbody.ve-par tr:first-child td { border-top: 2px solid #cbd5e1; }
    .ve-grilla tbody[hidden] { display: none; }
    .ve-grilla td.num { text-align: right; font-variant-numeric: tabular-nums; }
    .ve-grilla td.cuit, .ve-grilla td.comp { font-family: Consolas, 'Cascadia Mono', monospace; font-size: 13px; font-variant-numeric: tabular-nums; }
    .ve-grilla td.origen, .ve-grilla td.op { font-size: 11px; font-weight: 700; }
    .ve-grilla td.op { color: #64748b; }
    .ve-grilla td.origen-arca { color: #1b2f57; }
    .ve-grilla td.origen-xubio { color: #217346; }
    .ve-grilla td.marca.error { background: #fdeaea; box-shadow: inset 0 0 0 1.5px #c53030; }
    .ve-grilla td.marca.ok { background: #e7f5ec; box-shadow: inset 0 0 0 1.5px #15803d; }
    .ve-grilla td.marca.aviso { background: #fff7e6; }
    /* La corrección en una sola línea (el importe con su diferencia incluido). */
    .ve-grilla td.corr { white-space: nowrap; text-overflow: clip; vertical-align: middle; background: #fffafa; border-left: 1px solid #e2e8f0; font-size: 12.5px; font-weight: 600; line-height: 1.9; }
    .ve-corr-et { color: #475569; }
    .ve-valor { display: inline-block; padding: 0 6px; border-radius: 4px; color: #fff; font-weight: 700; font-size: 12px; line-height: 1.6; white-space: nowrap; }
    .ve-valor-error { background: #c53030; }
    .ve-valor-ok { background: #15803d; }
    .ve-flecha { font-weight: 800; color: #475569; }
    .ve-diferencia { color: #64748b; font-weight: 600; white-space: nowrap; }
    .ve-nota { display: inline-block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; vertical-align: middle; font-size: 12.5px; font-weight: 700; padding: 2px 10px; border-radius: 999px; }
    .ve-nota-neutro { background: #eef2f7; color: #475569; }
    .ve-nota-azul { background: #e8eef9; color: #1d4ed8; }
    .letra { display: inline-block; min-width: 16px; text-align: center; border-radius: 3px; color: #fff; font-weight: 700; padding: 0 3px; }
    .letra-error { background: #c53030; }
    .letra-ok { background: #15803d; }

    /* Ventanas medianas: en la tabla de errores (con «Corrección en Xubio») la CUIT
       cede su lugar a la razón social (está en el Excel). */
    @media (max-width: 1100px) {
      .ve-grilla table.con-corr col.c-cuit { width: 0; }
      .ve-grilla table.con-corr td.cuit, .ve-grilla table.con-corr th.h-cuit { padding: 0; }
    }

    @media (max-width: 720px) {
      .ve-popup .swal2-html-container { padding: 12px 12px 14px; }
      .ve-popup .swal2-title { padding: 18px 64px 12px 16px; font-size: 1.25em; }
      .ve-popup .swal2-actions { padding: 12px 16px; }
      .ve-popup .swal2-confirm { width: 100%; }
      .ve-barra { top: -12px; margin: 0 -12px 12px; padding: 8px 12px; }
      .ve-chip { min-height: 44px; }
      .ve-derecha { max-width: none; }
      .ve-error summary { flex-wrap: wrap; }
      .ve-insignias { flex-direction: row; }
      .ve-cab { flex-basis: 100%; }
      .ve-derecha { width: 100%; justify-content: flex-start; }
      /* En pantallas angostas la CUIT cede su lugar (está en el Excel de la auditoría). */
      .ve-grilla col.c-cuit { width: 0; }
      .ve-grilla td.cuit, .ve-grilla th.h-cuit { padding: 0; }
      /* En el celular no entra en una línea: ahí sí se parte. */
      .ve-grilla col.c-corr, .ve-grilla col.c-corr-ancha { width: 150px; }
      .ve-grilla td.corr { white-space: normal; }
    }
  </style>`;

/** Cuántos errores hay por gravedad. */
export function contarErrores(errores: readonly Advertencia[]): Record<Advertencia['gravedad'], number> {
  const n = { grave: 0, corregir: 0, info: 0 };
  for (const e of errores) n[e.gravedad] = (n[e.gravedad] ?? 0) + 1;
  return n;
}

/**
 * Lo que hay que gestionar en una operación: Error + Subir a Xubio + A revisar, en
 * comprobantes, sin las que el control ya explicó (tiques, extractos, exterior, o ya en «Error»).
 */
export function pendientesDe(errores: readonly Advertencia[], cliente: string, operacion: string, hojas?: HojasExcel): number {
  const clave = claveExcel(cliente, operacion);
  const solo = (h: FaltantesDelExcel | undefined) => (h?.get(clave) ? new Map([[clave, h.get(clave)!]]) : undefined);
  const n = contarBloques(
    errores.filter((e) => e.cliente === cliente && e.operacion === operacion),
    solo(hojas?.sinLibro),
    solo(hojas?.sinArca),
  );
  return n.error + n.soloArca + n.soloXubio - n.explicadas;
}

/** La tira de KPIs: una tarjeta por operación con cuántos coinciden y cuántos hay que gestionar. */
function bloqueResumen(resumenes: readonly ResumenOperacion[], varios: boolean, errores: readonly Advertencia[], hojas?: HojasExcel): string {
  if (!resumenes.length) return '';
  const tarjetas = resumenes
    .map((r) => {
      const pendientes = pendientesDe(errores, r.cliente, r.operacion, hojas);
      const ok = pendientes === 0;
      const estado = ok ? '✓ Sin pendientes' : `${pendientes} a gestionar`;
      const quien = varios ? `<span class="ve-kpi-cliente">${esc(r.cliente)} · </span>` : '';
      const pct = r.arca ? Math.round((r.coinciden / r.arca) * 100) : 100;
      return `<li class="ve-kpi ${ok ? 've-kpi-ok' : 've-kpi-mal'}">
        <span class="ve-kpi-cab"><span class="ve-kpi-op">${quien}${esc(r.operacion)}</span><span class="ve-kpi-estado">${estado}</span></span>
        <span class="ve-kpi-num"><b>${r.coinciden}</b> de ${r.arca} comprobantes de ARCA coinciden con Xubio</span>
        <span class="ve-kpi-barra" role="img" aria-label="${pct}% coincide"><span style="width:${pct}%"></span></span>
      </li>`;
    })
    .join('');
  return `<ul class="ve-kpis" aria-label="Resumen por operación">${tarjetas}</ul>`;
}

/**
 * Filtros globales, por gravedad. Los que no tienen nada quedan deshabilitados.
 * `sueltas`: los hallazgos fuera de los bloques (un paso que falló, por ejemplo), que
 * se ven con cualquier filtro: entran en «Todos» para que el número coincida con lo
 * que hay en la lista.
 */
function filtrosGlobales(n: ConteoBloques, sueltas: number): string {
  const chips: [string, string, number][] = [
    ['', 'Todos', sueltas + n.error + n.soloArca + n.soloXubio],
    ['error', 'Error', n.error],
    ['subir', 'Subir a Xubio', n.soloArca],
    ['revisar', 'A revisar', n.soloXubio],
  ];
  return `<div class="ve-filtros" role="group" aria-label="Filtrar hallazgos">${chips
    .map(
      ([id, nombre, k]) =>
        `<button type="button" class="ve-chip" data-ver="${id}" aria-pressed="${id === ''}"${id && !k ? ' disabled' : ''}>${nombre} <span class="ve-chip-num">${k}</span></button>`,
    )
    .join('')}</div>`;
}

/** Botones que eligen uno de varios (pills, pestañas, filtros): marca el elegido y avisa. */
function grupoDeBotones(botones: NodeListOf<HTMLButtonElement>, atributo: 'aria-pressed' | 'aria-selected', alElegir: (b: HTMLButtonElement) => void): void {
  for (const b of botones) {
    b.addEventListener('click', (ev) => {
      ev.preventDefault();
      for (const otro of botones) otro.setAttribute(atributo, String(otro === b));
      alElegir(b);
    });
  }
}

/**
 * Abre la ventana de hallazgos. Devuelve la promesa de SweetAlert2 (se resuelve al cerrarla).
 * `canEdit` false (vista Empleado): los botones de resolución quedan deshabilitados y
 * «Volver a correr» pasa a ser una etiqueta (no hay Nuevo control).
 */
export function mostrarVentanaDeErrores(
  errores: readonly Advertencia[],
  resumenes: readonly ResumenOperacion[] = [],
  canEdit = true,
  /** Si viene, las partidas de ARCA no encontradas llevan «Subir a Xubio» (cierra la ventana y sube). */
  alSubirAXubio?: (a: Advertencia) => void,
  /** Para recordar (en este navegador) qué filas se marcaron como cargadas / resueltas. */
  ejecucionId?: string,
  /** Lo que no cruzó, tal como está en los AUX («Subir a Xubio» y «A revisar»). */
  hojas?: HojasExcel,
  /** «Aplicar cambios»: cuántos hay tildados en todo el control y qué hacer al tocarlo (cierra la ventana). */
  aplicar?: { contar: () => number; alAplicar: () => void },
  /** Mails de los clientes y período, para «Consultar al cliente». */
  ctx: ContextoVentana = { emails: {}, periodo: '' },
) {
  contexto = ctx;
  const hechas = leerHechas(ejecucionId);
  const graves = contarErrores(errores).grave;
  const conteo = contarBloques(errores, hojas?.sinLibro, hojas?.sinArca);
  const ordenados = ordenarHallazgos(errores);
  const porCliente = new Map<string, Advertencia[]>();
  for (const e of ordenados) porCliente.set(e.cliente, [...(porCliente.get(e.cliente) ?? []), e]);
  // Un cliente sin hallazgos pero con partidas en el Excel también aparece.
  for (const [clave, filas] of [...(hojas?.sinLibro ?? []), ...(hojas?.sinArca ?? [])]) {
    const cliente = clave.slice(0, clave.lastIndexOf('|'));
    if (filas.length && !porCliente.has(cliente)) porCliente.set(cliente, []);
  }
  const unSoloCliente = porCliente.size === 1;
  // «Subir a Xubio» puede venir de un hallazgo armado desde la hoja: se guardan acá.
  const subibles: Advertencia[] = [];
  const registrar = (a: Advertencia) => subibles.push(a) - 1;

  const hayAlgo = errores.length > 0 || porCliente.size > 0;
  // Los filtros solo separan comprobantes (Error / Subir a Xubio / A revisar). Si no
  // hay ninguno (por ejemplo, solo fallaron los bots) no hay nada que filtrar: antes
  // aparecía «Todos (0)» al lado de tres errores graves.
  const hayComprobantes = conteo.error + conteo.soloArca + conteo.soloXubio > 0;
  const filtros = hayAlgo && hayComprobantes ? filtrosGlobales(conteo, errores.filter(esSuelta).length) : '';
  const barra = filtros ? `<div class="ve-barra">${filtros}</div>` : '';
  // Debajo del título: de quién y de qué período (con un solo cliente, su nombre).
  const sub = [unSoloCliente ? [...porCliente.keys()][0] : porCliente.size ? `${porCliente.size} clientes` : '', ctx.periodo && `Período ${ctx.periodo}`]
    .filter(Boolean)
    .map((t) => esc(t))
    .join(' · ');
  const lista = [...porCliente.entries()]
    .map(
      ([cliente, items]) => {
        const marcado: Marcado = { canEdit, hechas, ejecucionId, cliente, conAplicar: !!aplicar };
        return `<section class="ve-grupo" data-cliente="${esc(cliente)}">
        ${unSoloCliente ? '' : `<p class="ve-cliente">${esc(cliente)}</p>`}
        ${items.filter(esSuelta).map((e) => tarjeta(e, canEdit)).join('')}
        ${bloqueSubir(items, cliente, excelDelCliente(hojas?.sinLibro, cliente), canEdit, registrar, !!alSubirAXubio, marcado)}
        ${bloqueError(items, marcado)}
        ${bloqueRevisar(items, cliente, excelDelCliente(hojas?.sinArca, cliente), marcado)}
      </section>`;
      },
    )
    .join('');

  return Swal.fire({
    title: hayAlgo ? 'Hallazgos del control' : 'Control sin hallazgos',
    html: `${ESTILOS}
      <div class="ve">
        ${sub ? `<p class="ve-sub">${sub}</p>` : ''}
        ${
          graves
            ? `<div class="ve-grave-aviso" role="alert"><span class="ve-grave-icono" aria-hidden="true">!</span><span><strong>El control terminó con ${graves === 1 ? 'un error grave' : `${graves} errores graves`}.</strong> Hasta corregirlo, el resultado no es confiable.</span></div>`
            : ''
        }
        ${bloqueResumen(resumenes, new Set(resumenes.map((r) => r.cliente)).size > 1, errores, hojas)}
        ${barra}
        ${lista}
      </div>`,
    width: 'min(1080px, 96vw)',
    customClass: { popup: 've-popup' },
    confirmButtonText: 'Cerrar',
    showCloseButton: true,
    closeButtonAriaLabel: 'Cerrar los hallazgos',
    confirmButtonColor: '#2563eb',
    focusConfirm: true,
    didOpen: (popup) => {
      // «Subir a Xubio»: cierra la ventana y sube.
      for (const b of popup.querySelectorAll<HTMLButtonElement>('button[data-subir]')) {
        b.addEventListener('click', (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          const a = subibles[Number(b.dataset['subir'])];
          Swal.close();
          if (a && alSubirAXubio) alSubirAXubio(a);
        });
      }
      // Filtros globales: muestran un solo bloque (lo grave queda siempre a la vista).
      grupoDeBotones(popup.querySelectorAll<HTMLButtonElement>('button[data-ver]'), 'aria-pressed', (b) => {
        const ver = b.dataset['ver'];
        for (const bloque of popup.querySelectorAll<HTMLElement>('.ve-bloque')) bloque.hidden = !!ver && bloque.dataset['bloque'] !== ver;
        for (const grupo of popup.querySelectorAll<HTMLElement>('.ve-grupo')) {
          grupo.hidden = !!ver && !grupo.querySelector(`.ve-bloque[data-bloque="${ver}"], details:not(.ve-bloque)`);
        }
      });
      // Pills del bloque «Error»: Letra / Tipo / Importe…
      for (const pills of popup.querySelectorAll<HTMLElement>('.ve-pills')) {
        const bloque = pills.closest('.ve-bloque');
        grupoDeBotones(pills.querySelectorAll<HTMLButtonElement>('button[data-pill]'), 'aria-pressed', (b) => {
          const clase = b.dataset['pill'];
          for (const par of bloque?.querySelectorAll<HTMLElement>('tbody.ve-par') ?? []) par.hidden = !!clase && par.dataset['clase'] !== clase;
        });
      }
      // Tilde «cargado / resuelto» (solo Acceso Total; el empleado lo ve deshabilitado).
      const marcadas = new Set(hechas);
      for (const caja of popup.querySelectorAll<HTMLInputElement>('input[data-hecho]')) {
        caja.addEventListener('change', () => {
          if (!canEdit) return;
          const clave = caja.dataset['hecho']!;
          if (caja.checked) marcadas.add(clave);
          else marcadas.delete(clave);
          caja.closest('tbody')?.classList.toggle('ve-hecha', caja.checked);
          guardarHechas(ejecucionId, marcadas);
        });
      }
      // Qué se sube: checkbox por fila en «Subir a Xubio» y «Usar dato ARCA / Mantener
      // Xubio» en «Error» (si el par está en «Subir a Xubio», son la misma elección).
      const grupoDe = (el: Element) => el.closest<HTMLElement>('.ve-grupo');
      const sincronizar = (grupo: HTMLElement | null) => {
        if (!grupo) return;
        const cajas = [...grupo.querySelectorAll<HTMLInputElement>('input[data-incluir]')];
        for (const c of cajas) c.closest('tbody')?.classList.toggle('ve-excluida', !c.checked);
        const tildadas = cajas.filter((c) => c.checked).length;
        const cuenta = grupo.querySelector('[data-cuenta]');
        if (cuenta) cuenta.textContent = `${tildadas} de ${cajas.length} para subir`;
        const todas = grupo.querySelector<HTMLInputElement>('input[data-incluir-todas]');
        if (todas) {
          todas.checked = tildadas === cajas.length;
          todas.indeterminate = tildadas > 0 && tildadas < cajas.length;
        }
        if (canEdit) {
          for (const b of grupo.querySelectorAll<HTMLButtonElement>('button[data-subir-op]')) {
            b.disabled = !cajas.some((c) => c.checked && c.dataset['op'] === b.dataset['subirOp']);
          }
        }
      };
      const tildar = (caja: HTMLInputElement) => {
        const cliente = grupoDe(caja)?.dataset['cliente'] ?? '';
        incluir(ejecucionId, cliente, caja.dataset['op'] ?? '', caja.dataset['fila'] ?? '', caja.checked);
      };
      for (const caja of popup.querySelectorAll<HTMLInputElement>('input[data-incluir]')) {
        caja.addEventListener('change', () => {
          if (!canEdit) return;
          tildar(caja);
          sincronizar(grupoDe(caja));
        });
      }
      for (const todas of popup.querySelectorAll<HTMLInputElement>('input[data-incluir-todas]')) {
        todas.addEventListener('change', () => {
          if (!canEdit) return;
          for (const caja of todas.closest('table')?.querySelectorAll<HTMLInputElement>('input[data-incluir]') ?? []) {
            caja.checked = todas.checked;
            tildar(caja);
          }
          sincronizar(grupoDe(todas));
        });
      }
      // «Aplicar cambios»: tildar solo elige; el cambio lo hace el servidor al tocar el botón.
      const contarAplicar = () => {
        const n = aplicar?.contar() ?? 0;
        for (const cuenta of popup.querySelectorAll('[data-aplicar-cuenta]')) {
          cuenta.textContent = n ? `${n} ${n === 1 ? 'cambio seleccionado listo' : 'cambios seleccionados listos'} para aplicar` : 'Tildá los pares que querés corregir con el dato de ARCA.';
        }
        for (const b of popup.querySelectorAll<HTMLButtonElement>('button[data-aplicar-btn]')) {
          b.textContent = n ? `Aplicar cambios (${n})` : 'Aplicar cambios';
          if (canEdit) b.disabled = !n;
        }
        for (const tabla of popup.querySelectorAll('table')) {
          const cajas = [...tabla.querySelectorAll<HTMLInputElement>('input[data-aplicar]')];
          const todos = tabla.querySelector<HTMLInputElement>('input[data-aplicar-todos]');
          if (!todos || !cajas.length) continue;
          const k = cajas.filter((c) => c.checked).length;
          todos.checked = k === cajas.length;
          todos.indeterminate = k > 0 && k < cajas.length;
        }
      };
      const elegir = (cajas: HTMLInputElement[], si: boolean) => {
        elegirParaAplicar(ejecucionId, cajas.map((c) => c.dataset['aplicar'] ?? ''), si);
        for (const c of cajas) {
          c.checked = si;
          c.closest('tbody')?.classList.toggle('ve-elegido', si);
        }
        contarAplicar();
      };
      for (const caja of popup.querySelectorAll<HTMLInputElement>('input[data-aplicar]')) {
        caja.addEventListener('change', () => {
          if (canEdit) elegir([caja], caja.checked);
        });
      }
      for (const todos of popup.querySelectorAll<HTMLInputElement>('input[data-aplicar-todos]')) {
        todos.addEventListener('change', () => {
          if (canEdit) elegir([...(todos.closest('table')?.querySelectorAll<HTMLInputElement>('input[data-aplicar]') ?? [])], todos.checked);
        });
      }
      for (const b of popup.querySelectorAll<HTMLButtonElement>('button[data-aplicar-btn]')) {
        b.addEventListener('click', (ev) => {
          ev.preventDefault();
          if (!canEdit || !aplicar?.contar()) return;
          Swal.close();
          aplicar.alAplicar();
        });
      }
      contarAplicar();
      for (const grupo of popup.querySelectorAll<HTMLElement>('.ve-grupo')) sincronizar(grupo);
      // Copia rápida: clic en el comprobante o la CUIT.
      popup.addEventListener('click', (ev) => {
        const celda = (ev.target as HTMLElement).closest<HTMLElement>('[data-copiar]');
        if (!celda) return;
        void navigator.clipboard
          ?.writeText(celda.dataset['copiar'] ?? '')
          .then(() => {
            celda.classList.add('ve-copiado');
            setTimeout(() => celda.classList.remove('ve-copiado'), 900);
          })
          .catch(() => undefined);
      });
      // La prioritaria no se pliega (los botones de adentro siguen andando).
      for (const d of popup.querySelectorAll<HTMLDetailsElement>('details.ve-prioridad')) {
        d.querySelector('summary')?.addEventListener('click', (ev) => {
          if (!(ev.target as HTMLElement).closest('a, button')) ev.preventDefault();
        });
      }
    },
    // Sin animación: si la pestaña está atrás (mirando el Chrome del bot), el
    // navegador congela las animaciones y la ventana quedaba transparente.
    showClass: { popup: '', backdrop: 'swal2-backdrop-show', icon: '' },
    hideClass: { popup: '', backdrop: '', icon: '' },
  });
}
