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
      const op = g.filas[0]?.op;
      const atributos = `${clases ? ` class="${clases}"` : ''}${g.clase ? ` data-clase="${g.clase}"` : ''}${op ? ` data-op="${esc(op)}"` : ''}`;
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
  /** Cuenta de Gmail con la que se redacta (`authuser`): el usuario de Xubio del control. */
  cuenta?: string;
  /** Con el backend en esta PC: le pide que apriete Ctrl+V en Gmail cuando se abra. */
  pegarEnGmail?: () => Promise<void>;
  /** El día con el que se arma el saludo final (por defecto, hoy). */
  hoy?: Date;
  /**
   * «Pedir que las carguen»: descarga el Excel de las partidas de ARCA no encontradas
   * (formato «Mis Comprobantes») para adjuntarlo; con operación, solo esa.
   */
  descargarExcelFaltantes?: (cliente: string, operacion: string | null) => Promise<void>;
  /** «Descargar hallazgos»: baja los avisos en un Excel (del cliente elegido, o de todos). */
  descargarHallazgos?: (cliente: string | null) => Promise<void>;
}

/**
 * Las facturas de ARCA que no estaban en Xubio, dos avisos al cliente:
 * - `cargadas`: el estudio ya las subió; faltan percepciones, centro de costo y pago.
 * - `a_cargar`: el cliente las carga él mismo; se le dice cuáles.
 */
export type AvisoCarga = 'cargadas' | 'a_cargar';

/** El saludo final según el día: lunes, comienzo de semana; viernes a domingo, fin de semana. */
export function saludoFinal(hoy: Date = new Date()): string {
  const dia = hoy.getDay(); // 0 domingo … 6 sábado
  if (dia === 1) return 'Saludos cordiales y buen comienzo de semana!!';
  if (dia === 5 || dia === 6 || dia === 0) return 'Saludos cordiales y buen fin de semana!!';
  return 'Saludos cordiales y buena semana!!';
}

/** El de la ventana abierta (se fija al abrirla). */
let contexto: ContextoVentana = { emails: {}, periodo: '' };

/**
 * «Consultar al cliente»: arma el mail con la estructura del estudio (saludo,
 * pedido, tabla de comprobantes con bordes y cierre) y abre el compose de Gmail
 * con la cuenta de la empresa (`authuser`), el destinatario y el asunto.
 *
 * El cuerpo NO va en la URL: Gmail solo acepta texto plano ahí y la tabla
 * llegaba como «a | b | c». El mail con la tabla (HTML) se copia al
 * portapapeles y se pega con Ctrl+V; Gmail abierto vacío pone la firma debajo.
 * Solo si no se puede copiar, el texto plano va en la URL (si entra).
 *
 * Se usa el compose de Gmail y no `mailto:` porque Windows resuelve `mailto:`
 * con la aplicación de correo predeterminada del sistema (en esta PC, Outlook),
 * y Gmail en el navegador nunca llegaba a abrirse.
 */
const COMPOSE_GMAIL = 'https://mail.google.com/mail/?view=cm&fs=1';

/** Hasta cuántos caracteres de cuerpo van en la URL del compose (respaldo sin portapapeles). */
export const MAX_CUERPO_URL = 1800;

/** El mail listo: link al compose de Gmail, el cuerpo con la tabla (HTML) y en texto. */
export interface MailArmado {
  /** Compose de Gmail: cuenta, destinatario y asunto (sin cuerpo). */
  url: string;
  /** Destinatarios («a@b.com,c@d.com»); vacío si el cliente no tiene mail. */
  para: string;
  /** Respaldo si no se pudo copiar: el mismo compose con el texto plano, o null si es largo. */
  urlConTexto: string | null;
  html: string;
  texto: string;
}

const MAIL_VALIDO = /^[^@\s,]+@[^@\s,]+\.[^@\s,]+$/;

/** URL del compose de Gmail; `URLSearchParams` codifica saltos de línea y acentos. */
export function urlComposeGmail(p: { cuenta?: string; para?: string; asunto: string; cuerpo?: string }): string {
  const q = new URLSearchParams();
  // authuser: Gmail redacta con esa cuenta (la de la empresa, con su firma)
  // aunque el navegador tenga otras abiertas. Solo si es un mail.
  const cuenta = (p.cuenta ?? '').trim();
  if (MAIL_VALIDO.test(cuenta)) q.set('authuser', cuenta);
  if (p.para) q.set('to', p.para);
  q.set('su', p.asunto);
  if (p.cuerpo) q.set('body', p.cuerpo);
  // URLSearchParams pasa los espacios a «+»; con %20 no hay dudas al decodificar.
  return `${COMPOSE_GMAIL}&${q.toString().replace(/\+/g, '%20')}`;
}

/** Los mails armados de la ventana abierta, por id del botón. */
const mailsArmados = new Map<string, MailArmado>();
/** Los que van con el Excel de partidas de ARCA no encontradas: de qué cliente y operación. */
const adjuntos = new Map<string, { cliente: string; operacion: string | null }>();

/** Columnas de importe: «62.502,25»; en cero, vacías o nulas (Factura C) «-». */
const COLUMNAS_IMPORTE = /neto|exent|iva|total|suma|diferencia/i;
/** Códigos y documentos: centrados en la tabla del mail. */
const COLUMNAS_CODIGO = /punto de venta|número desde|numero desde|autoriz|tipo doc|nro\. doc|cuit|comprobante/i;

function celdaMail(columna: string, v: CeldaVista['valor'] | undefined): string {
  if (v === null || v === undefined || v === '') return COLUMNAS_IMPORTE.test(columna) ? '-' : '';
  if (COLUMNAS_IMPORTE.test(columna)) {
    const n = aNumero(v);
    if (n === null) return String(v);
    return n === 0 ? '-' : n.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  // Punto de venta, número, CAE y CUIT: enteros, sin separadores de miles.
  if (typeof v === 'number') return String(v);
  return String(v);
}

/** El cuadro del mail: títulos y, por fila, cada celda ya como texto. */
export interface TablaMail {
  columnas: string[];
  datos: string[][];
}

/** «IVA 21%», «IVA 10,5%»…: el IVA de una alícuota (el número es el porcentaje). */
const COLUMNA_ALICUOTA = /^\s*(?:imp\.?\s*)?iva\s*(\d+(?:[.,]\d+)?)\s*%?\s*$/i;
/** Las tres columnas de ARCA que en el mail van juntas en «Comprobante». */
const PARTES_COMPROBANTE = ['Tipo', 'Punto de Venta', 'Número Desde'] as const;

/** «30707310657» → «30-70731065-7». Lo que no tiene 11 dígitos (un DNI) queda igual. */
function cuitConGuiones(v: CeldaVista['valor'] | undefined): string {
  const t = v === null || v === undefined ? '' : String(v).trim();
  const d = t.replace(/\D/g, '');
  return d.length === 11 ? `${d.slice(0, 2)}-${d.slice(2, 10)}-${d.slice(10)}` : t;
}

/** «11 - Factura C», 1, 7 → «Factura C-00001-00000007». */
function comprobanteMail(tipo: CeldaVista['valor'] | undefined, pv: CeldaVista['valor'] | undefined, numero: CeldaVista['valor'] | undefined): string {
  const nombre = String(tipo ?? '').replace(/^\s*\d+\s*-\s*/, '').trim();
  const digitos = (v: CeldaVista['valor'] | undefined, largo: number) => {
    const d = String(v ?? '').replace(/\D/g, '');
    return d ? d.padStart(largo, '0') : '';
  };
  const p = digitos(pv, 5);
  const n = digitos(numero, 8);
  return p && n ? `${nombre}-${p}-${n}` : nombre;
}

/**
 * La tabla del mail: las columnas de ARCA si vinieron; si no (controles viejos), las de la vista.
 * Con las de ARCA: Tipo, Punto de Venta y Número Desde van juntas en «Comprobante»; el
 * documento, con guiones; y la celda IVA dice el porcentaje de cada alícuota (varias, una
 * debajo de la otra).
 */
export function tablaMail(filas: FilaConError[]): TablaMail {
  const conMail = filas.find((f) => f.mail?.length);
  const base = (conMail?.mail ?? filas[0]?.fila ?? []).map((c) => c.columna);
  const alicuotas = base.filter((c) => COLUMNA_ALICUOTA.test(c));
  const juntar = PARTES_COMPROBANTE.every((c) => base.includes(c));
  const columnas = base.flatMap((c) => {
    if (alicuotas.includes(c)) return [];
    if (juntar && c === 'Tipo') return ['Comprobante'];
    if (juntar && (PARTES_COMPROBANTE as readonly string[]).includes(c)) return [];
    return [c];
  });
  const datos = filas.map((f) => {
    const valores: Record<string, CeldaVista['valor']> = {};
    for (const c of (conMail ? f.mail : undefined) ?? f.fila) valores[c.columna] = c.valor;
    return columnas.map((col) => {
      if (juntar && col === 'Comprobante') return comprobanteMail(valores['Tipo'], valores['Punto de Venta'], valores['Número Desde']);
      if (/^nro\. doc/i.test(col)) return cuitConGuiones(valores[col]);
      if (col === 'IVA' && alicuotas.length) {
        const partes = alicuotas
          .map((a) => [a.match(COLUMNA_ALICUOTA)![1], aNumero(valores[a] ?? null)] as const)
          .filter(([, n]) => n)
          .map(([pct, n]) => `${pct}%: ${celdaMail('IVA', n)}`);
        if (partes.length) return partes.join('\n');
      }
      return celdaMail(col, valores[col]);
    });
  });
  return { columnas, datos };
}

// Plantilla oficial del estudio: todas las celdas con borde, para que al pegar
// en Gmail las columnas no queden sueltas.
const ESTILO_TABLA = 'border-collapse:collapse;border:1px solid #64748b;font-family:Calibri,Arial,sans-serif;font-size:13px';
const ESTILO_CELDA = 'border:1px solid #94a3b8;padding:5px 8px';
const ESTILO_TH = `${ESTILO_CELDA};background-color:#e2e8f0;font-weight:bold;text-align:center;white-space:nowrap`;

/** Filas alternadas, como una tabla de Excel. */
const FONDO_PAR = 'background-color:#f8fafc';

/** Importes a la derecha (el total en negrita), códigos y CUIT al centro. */
function estiloTd(columna: string): string {
  if (COLUMNAS_IMPORTE.test(columna)) {
    return `${ESTILO_CELDA};text-align:right;white-space:nowrap${/^(imp\. )?total$/i.test(columna.trim()) ? ';font-weight:bold' : ''}`;
  }
  if (COLUMNAS_CODIGO.test(columna)) return `${ESTILO_CELDA};text-align:center;white-space:nowrap`;
  return `${ESTILO_CELDA};text-align:left`;
}

/**
 * `tabla`: el cuadro ya armado (por ejemplo, el de «A revisar», igual que la
 * grilla de la ventana). Sin ella, se arma con los comprobantes del hallazgo.
 */
export function armarMail(e: Advertencia, ctx: ContextoVentana, tabla?: TablaMail, aviso: AvisoCarga = 'cargadas'): MailArmado {
  // Sin operación (el mail de «Todos» en «A revisar»): van las dos juntas.
  const operacion = e.operacion ? e.operacion.toLowerCase() : 'ventas y compras';
  const pedido =
    e.tipo === 'total_no_cierra'
      ? [
          `Enviamos detalles de facturas de ${operacion} en las que, en ARCA, el neto, el IVA y los demás conceptos no suman el total. Por favor, indicarnos a qué corresponde la diferencia (impuestos internos, combustibles u otro concepto) y enviarnos las facturas en cuanto las tengan.`,
        ]
      : e.tipo === 'faltante_en_arca'
        ? [
            `Enviamos detalles de comprobantes de ${operacion} que tenemos registrados y que no figuran en ARCA. Por favor, confirmarnos si corresponden y, si es así, enviarnos los comprobantes en cuanto los tengan.`,
          ]
        : aviso === 'a_cargar'
          ? [
              `Enviamos detalles de facturas de ${operacion} que están en Arca y que todavía no figuran en Xubio. Por favor cargarlas en cuanto puedan, junto con sus percepciones.`,
              'Al cargarlas, tener en cuenta a qué centro de costo corresponden y, en el caso que fueron abonadas, de qué forma fueron canceladas.',
            ]
          : [
              'Enviamos detalles de facturas que están en Arca y que fueron cargadas a Xubio; falta cargar las percepciones. Por favor enviarlas en cuanto las tengan.',
              'También aclarar en la factura a qué centro de costo imputamos y, en el caso que fueron abonadas, aclarar de qué forma fueron canceladas.',
            ];
  const saludo = 'Estimados buenos días, ¿cómo están?';
  const cierre = ['Quedamos a la espera, muchas gracias.', saludoFinal(ctx.hoy)];
  const { columnas, datos } = tabla ?? tablaMail((e.comprobantes as FilaConError[]).filter((f) => 'fila' in f && !f.motivo));
  const estilos = columnas.map(estiloTd);

  const parrafo = (t: string) => `<p style="margin:0 0 12px">${esc(t)}</p>`;
  // border="1" además del estilo: si Gmail descarta algún estilo al pegar, los bordes quedan.
  const cuadro =
    `<table border="1" cellspacing="0" cellpadding="5" style="${ESTILO_TABLA}"><thead><tr>` +
    columnas.map((c) => `<th style="${ESTILO_TH}">${esc(c)}</th>`).join('') +
    '</tr></thead><tbody>' +
    datos
      .map((fila, n) => `<tr${n % 2 ? ` style="${FONDO_PAR}"` : ''}>` + fila.map((v, i) => `<td style="${estilos[i]}">${esc(v).replace(/\n/g, '<br>')}</td>`).join('') + '</tr>')
      .join('') +
    '</tbody></table>';
  const html =
    '<div style="font-family:Arial,sans-serif;font-size:13px">' +
    parrafo(saludo) +
    pedido.map(parrafo).join('') +
    cuadro +
    '<br>' +
    cierre.map(parrafo).join('') +
    '</div>';
  const texto = [
    saludo,
    '',
    ...pedido.flatMap((t) => [t, '']),
    columnas.join(' | '),
    ...datos.map((f) => f.map((v) => v.replace(/\n/g, ' / ') || ' ').join(' | ')),
    '',
    ...cierre.flatMap((t) => [t, '']),
  ]
    .join('\n')
    .trimEnd();

  const asunto =
    aviso === 'a_cargar' && e.tipo !== 'total_no_cierra' && e.tipo !== 'faltante_en_arca'
      ? `Comprobantes de ${operacion} para cargar en Xubio · ${ctx.periodo} · ${e.cliente}`
      : `Consulta de comprobantes de ${operacion} · ${ctx.periodo} · ${e.cliente}`;
  const para = (ctx.emails[e.cliente] ?? '').split(',').map((m) => m.trim()).filter(Boolean).join(',');
  const base = { cuenta: ctx.cuenta, para, asunto };
  const url = urlComposeGmail(base);
  const urlConTexto = texto.length <= MAX_CUERPO_URL ? urlComposeGmail({ ...base, cuerpo: texto }) : null;
  return { url, para, urlConTexto, html, texto };
}

/**
 * Copia sincrónica del mail (con la tabla), el respaldo de `copiarMailConFormato`.
 * Antes era la única: con el `navigator.clipboard` asincrónico Gmail se abría
 * antes de terminar de copiar y quedaba vacío, solo con la firma. Ahora Gmail
 * se abre recién cuando la copia terminó.
 */
export function copiarMail(m: Pick<MailArmado, 'html' | 'texto'>): boolean {
  const alCopiar = (ev: ClipboardEvent) => {
    ev.clipboardData?.setData('text/html', m.html);
    ev.clipboardData?.setData('text/plain', m.texto);
    ev.preventDefault();
  };
  // execCommand('copy') necesita algo seleccionado para disparar el evento.
  const apoyo = document.createElement('textarea');
  apoyo.value = m.texto;
  apoyo.setAttribute('readonly', '');
  apoyo.style.cssText = 'position:fixed;left:-9999px;top:0;opacity:0';
  document.body.appendChild(apoyo);
  apoyo.select();
  document.addEventListener('copy', alCopiar);
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch {
    ok = false;
  } finally {
    document.removeEventListener('copy', alCopiar);
    apoyo.remove();
  }
  return ok;
}

/**
 * Copia el mail con formato (`text/html` con la tabla y `text/plain`) con
 * `navigator.clipboard.write`. Se espera a que termine ANTES de abrir Gmail: si
 * el navegador se abre primero, la app pierde el foco y la copia falla. Si el
 * portapapeles asincrónico no está (o no deja), se usa la copia sincrónica.
 */
export async function copiarMailConFormato(m: Pick<MailArmado, 'html' | 'texto'>): Promise<boolean> {
  try {
    if (navigator.clipboard?.write && typeof ClipboardItem !== 'undefined') {
      await navigator.clipboard.write([
        new ClipboardItem({
          'text/html': new Blob([m.html], { type: 'text/html' }),
          'text/plain': new Blob([m.texto], { type: 'text/plain' }),
        }),
      ]);
      return true;
    }
  } catch {
    // Sin permiso o sin foco: se prueba la copia sincrónica.
  }
  return copiarMail(m);
}

/** Aviso discreto abajo a la derecha, por encima de la ventana de hallazgos. */
export function avisoMail(texto: string, tipo: 'ok' | 'error' = 'ok'): HTMLElement {
  document.querySelector('.ve-aviso-mail')?.remove();
  const aviso = document.createElement('div');
  aviso.className = 've-aviso-mail';
  aviso.setAttribute('role', 'status');
  aviso.textContent = texto;
  aviso.style.cssText =
    'position:fixed;right:20px;bottom:20px;z-index:2000;max-width:min(420px,calc(100vw - 40px));padding:12px 16px;' +
    'border-radius:10px;font:600 14px/1.4 system-ui,sans-serif;color:#fff;box-shadow:0 8px 24px rgba(15,23,42,.3);' +
    `background:${tipo === 'ok' ? '#15803d' : '#b91c1c'}`;
  document.body.appendChild(aviso);
  setTimeout(() => aviso.remove(), 6000);
  return aviso;
}

/**
 * Clic en «Consultar al cliente»: copia el mail con la tabla y recién después
 * abre Gmail (cuenta, destinatario y asunto). Si Gmail se abre primero, la app
 * pierde el foco y la copia falla.
 *
 * `pegar`: con el backend en esta PC, él aprieta Ctrl+V cuando Gmail queda
 * adelante. Solo con destinatario: sin él, Gmail deja el cursor en «Para» y el
 * mail se pegaría ahí.
 */
export async function abrirMail(m: MailArmado, pegar?: () => Promise<void>, adjunto = ''): Promise<void> {
  if (await copiarMailConFormato(m)) {
    let solo = false;
    if (pegar && m.para) {
      try {
        await pegar();
        solo = true;
      } catch {
        // Sin backend local: queda el Ctrl+V a mano.
      }
    }
    avisoMail(
      solo
        ? `Correo con tabla copiado: se pega solo en Gmail en unos segundos (no cambies de ventana).${adjunto}`
        : `Correo con tabla copiado al portapapeles. Presiona Ctrl + V en Gmail${adjunto ? `.${adjunto}` : ''}`,
    );
    window.open(m.url, '_blank', 'noopener,noreferrer');
    return;
  }
  // Sin portapapeles: el texto plano en la URL (sin bordes, pero completo), si entra.
  avisoMail(
    m.urlConTexto
      ? 'No se pudo copiar la tabla: Gmail se abre con el texto del correo.'
      : 'No se pudo copiar el correo: Gmail se abre sin el texto.',
    'error',
  );
  window.open(m.urlConTexto ?? m.url, '_blank', 'noopener,noreferrer');
}

/** «✉ Consultar al cliente»: abre Gmail con el mail armado (o bloqueado en Empleado). */
function botonMail(e: Advertencia, canEdit: boolean, tabla?: TablaMail, conOperacion = false, aviso?: AvisoCarga): string {
  // Con Ventas y Compras en el mismo bloque hay un mail por operación: el botón dice cuál.
  const nombre = aviso === 'cargadas' ? 'Avisar que se cargaron' : aviso === 'a_cargar' ? 'Pedir que las carguen' : 'Consultar al cliente';
  const texto = `✉ ${nombre}${conOperacion && e.operacion ? ` · ${esc(e.operacion)}` : ''}`;
  if (!canEdit) return `<button type="button" class="ve-boton" disabled title="${BLOQUEADO}">${texto}</button>`;
  const destino = contexto.emails[e.cliente]
    ? `Abre Gmail con un mail a ${contexto.emails[e.cliente]}`
    : 'El cliente no tiene email cargado: se abre Gmail sin destinatario (cargalo en Clientes)';
  const m = armarMail(e, contexto, tabla, aviso);
  const queDice =
    aviso === 'cargadas'
      ? 'Avisa que ya las cargamos en Xubio y pide percepciones, centro de costo y forma de pago'
      : aviso === 'a_cargar'
        ? 'Le pide al cliente que las cargue él en Xubio y descarga el Excel de las partidas para adjuntarlo'
        : '';
  const ayuda = `${queDice ? `${queDice}. ` : ''}${destino}. El mail con la tabla queda copiado: pegalo en Gmail con Ctrl+V.`;
  const id = String(mailsArmados.size + 1);
  mailsArmados.set(id, m);
  // Al pedir que las carguen, va con el Excel de las partidas (se descarga para adjuntarlo).
  if (aviso === 'a_cargar') adjuntos.set(id, { cliente: e.cliente, operacion: e.operacion ?? null });
  return `<a class="ve-boton" data-mail="${id}" href="${esc(m.url)}" target="_blank" rel="noopener noreferrer" title="${esc(ayuda)}">${texto}</a>`;
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
  if (!canEdit) return `<button type="button" class="ve-boton ve-boton-chico" data-subir-op="${op}" disabled title="${BLOQUEADO}">${esc(texto)}</button>`;
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
    // Avisarle al cliente: que ya las cargamos (faltan percepciones, centro de costo y
    // forma de pago) o, si las carga él, cuáles tiene que cargar.
    if (adv && (adv.comprobantes as FilaConError[]).some((f) => 'fila' in f && !f.motivo)) {
      const varias = porOp.size > 1;
      botones.push(`<span data-op-mail="${op}">${botonMail(adv, canEdit, undefined, varias, 'cargadas')}${botonMail(adv, canEdit, undefined, varias, 'a_cargar')}</span>`);
    }
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
        <span class="ve-derecha ve-derecha-subir"><span class="ve-metas">${meta}</span>${botones.length ? `<span class="ve-acciones ve-acciones-subir">${botones.join('')}</span>` : ''}</span>
      </summary>
      <div class="ve-cuerpo">${grilla(grupos, marcado)}${notaMas(grupos.length, n, hoja)}</div>
    </details>`;
}

/**
 * El cuadro del mail de «A revisar»: las columnas y las filas de la grilla de la ventana.
 * `conOp`: con Ventas y Compras juntas, una primera columna dice de cuál es cada una.
 */
function tablaRevisar(grupos: Grupo[], conOp = false): TablaMail {
  const filas = grupos.map((g) => g.filas[0]);
  return {
    columnas: [...(conOp ? ['Operación'] : []), 'Fecha', 'Tipo', 'Comprobante', 'CUIT', 'Razón social', 'Observación', 'Total'],
    datos: filas.map((f) => [
      ...(conOp ? [f.op ?? ''] : []),
      texto(f.datos['Fecha']),
      abreviarTipo(texto(f.datos['Tipo'])),
      texto(f.datos['Comprobante']),
      cuit(f.datos['CUIT']),
      texto(f.datos['Razón social']),
      f.nota?.texto ?? '',
      importe(f.datos['Total']),
    ]),
  };
}

/** «A revisar»: partidas del Libro IVA no encontradas en ARCA, tal como están en el Excel. */
function bloqueRevisar(items: readonly Advertencia[], cliente: string, hojas: FaltantesDelExcel, marcado: Marcado): string {
  const { ops, grupos, porOp, hoja } = sinCruce(items, cliente, hojas, 'xubio');
  const n = [...porOp.values()].reduce((s, o) => s + o.n, 0);
  if (!n) return '';
  const explicadas = grupos.filter((g) => g.filas[0].nota).length;
  const meta = ops.length === 1 ? `<span class="ve-meta">${ops[0]}</span>` : '';
  // Un mail por operación, con el mismo cuadro que la grilla (tiques incluidos, con su observación).
  // Con Ventas y Compras, las pills eligen: «Todos» (lo predeterminado), con un solo
  // mail de las dos, o una operación, con sus filas y su mail.
  const separar = porOp.size > 1;
  const todas = tablaRevisar(grupos, true);
  const mailTodos =
    separar && todas.datos.length
      ? `<span class="ve-mail-op" data-op-mail="">${botonMail(
          { ...(porOp.values().next().value!.adv ?? faltanteDesdeExcel(cliente, ops[0], n)), tipo: 'faltante_en_arca', cliente, operacion: null } as Advertencia,
          marcado.canEdit,
          todas,
        )}</span>`
      : '';
  const mails = mailTodos + [...porOp]
    .map(([op, { n: k, adv }]) => {
      const tabla = tablaRevisar(grupos.filter((g) => g.filas[0].op === op));
      if (!tabla.datos.length) return '';
      const a: Advertencia = { ...(adv ?? faltanteDesdeExcel(cliente, op, k)), tipo: 'faltante_en_arca', cliente, operacion: op };
      const boton = botonMail(a, marcado.canEdit, tabla, separar);
      return separar ? `<span class="ve-mail-op" data-op-mail="${op}" hidden>${boton}</span>` : boton;
    })
    .join('');
  const pills = separar
    ? `<div class="ve-pills" data-pills-op role="group" aria-label="Elegir operación">
        <button type="button" class="ve-pill" data-op-pill="" aria-pressed="true">Todos (${n})</button>
        ${[...porOp].map(([op, { n: k }]) => `<button type="button" class="ve-pill" data-op-pill="${op}" aria-pressed="false">${op} (${k})</button>`).join('')}
      </div>`
    : '';
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
        <span class="ve-derecha">${meta}${mails ? `<span class="ve-acciones">${mails}</span>` : ''}${plegar(n)}</span>
      </summary>
      <div class="ve-cuerpo">${pills}${grilla(grupos, marcado)}${notaMas(grupos.length, n, hoja)}</div>
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
/** Las columnas que forman el Imp. Total: neto e IVA de cada alícuota («Neto
 * gravado 21%», «IVA 21%»…) o los totales, más no gravado, exento y otros tributos. */
const COLUMNA_QUE_SUMA = /^(neto gravado \d|iva \d|neto gravado total$|total iva$|neto no gravado$|op\. exentas$|otros tributos$)/i;

/**
 * «Las columnas no suman el total»: cada factura de ARCA con el neto y el IVA
 * de cada alícuota que usa, el no gravado, el exento y los otros tributos, el Imp.
 * Total, la diferencia (lo que ARCA no discrimina) y cómo quedó en Xubio.
 */
function grillaSuma(e: Advertencia): string {
  const filas = (e.comprobantes as FilaConError[]).filter((f) => 'fila' in f);
  if (!filas.length) return '';
  const partes = filas[0].fila.map((c) => c.columna).filter((c) => COLUMNA_QUE_SUMA.test(c.trim()));
  const cab = ['Fecha', 'Comprobante', 'Razón social', ...partes, 'Imp. Total', 'Diferencia', 'En Xubio']
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
      // Como en el Excel: la alícuota que no usa el comprobante, con «-».
      const columnas = partes.map((p) => `<td class="num">${v[p] ? esc(importe(v[p])) : '-'}</td>`).join('');
      return `<tr><td>${esc(texto(d.Fecha))}</td><td class="comp">${esc(texto(d.Comprobante))}</td><td title="${esc(texto(d.Tipo))} · CUIT ${esc(cuit(d.CUIT))}">${esc(texto(d['Razón social']))}</td>${columnas}<td class="num">${esc(importe(d.Total))}</td><td class="num marca aviso">${esc(importe(v['Diferencia']))}</td>${xubio}</tr>`;
    })
    .join('');
  return `<div class="ve-grilla"><table class="ve-suma"><thead><tr>${cab}</tr></thead><tbody>${cuerpo}</tbody></table></div>`;
}

/** El filtro de estado de una tarjeta suelta: la misma insignia que muestra. */
function verDeSuelta(e: Advertencia): 'error' | 'revisar' {
  return e.tipo === 'total_no_cierra' && e.gravedad !== 'info' ? 'revisar' : 'error';
}

function tarjeta(e: Advertencia, canEdit: boolean): string {
  const clase = e.tipo === 'total_no_cierra' && e.gravedad !== 'info' ? 'aviso' : claseDe(e);
  const n = e.total;
  const { botones, etiquetas } = botonesYEtiquetas(e, canEdit);
  const orden = ordenDe(e);
  return `
    <details class="ve-error ve-${clase}" data-suelta="${verDeSuelta(e)}"${e.operacion ? ` data-op="${esc(e.operacion)}"` : ''} open>
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
    .ve-cabeza { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; margin: 0 0 14px; }
    .ve-cabeza .ve-sub { margin: 0; }
    .ve-descargar:disabled { opacity: .6; cursor: progress; }
    .ve-sub { margin: 0 0 14px; color: #64748b; font-size: 14.5px; font-weight: 600; }

    /* Solo si hay errores graves: el resultado no es confiable. */
    .ve-grave-aviso { display: flex; align-items: center; gap: 12px; margin: 0 0 14px; padding: 12px 16px; border: 1px solid #fecaca; border-radius: 12px; background: #fef2f2; color: #7f1d1d; font-size: 14.5px; line-height: 1.4; }
    .ve-grave-icono { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; border-radius: 999px; background: #dc2626; color: #fff; font-weight: 900; }

    /* Resumen: una tarjeta por cliente, con Ventas y Compras adentro. */
    .ve-kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 300px), 1fr)); gap: 12px; margin: 0 0 18px; padding: 0; list-style: none; }
    .ve-kpi { display: flex; flex-direction: column; gap: 12px; padding: 14px 16px; border: 1px solid #e2e8f0; border-radius: 12px; background: #fff; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05); }
    .ve-kpi-cab { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
    .ve-kpi-nombre { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 15px; font-weight: 800; color: #0f172a; }
    .ve-kpi-mods { display: flex; flex-direction: column; gap: 12px; }
    /* Cada operación: etiqueta y el número grande. La raya: verde sin pendientes, ámbar con. */
    .ve-kpi-mod { display: flex; flex-direction: column; gap: 2px; padding-left: 10px; border-left: 3px solid #16a34a; }
    .ve-kpi-mod-mal { border-left-color: #f59e0b; }
    .ve-kpi-op { font-size: 12.5px; font-weight: 800; text-transform: uppercase; letter-spacing: .05em; color: #475569; }
    .ve-kpi-num { font-size: 14px; color: #475569; line-height: 1.35; }
    .ve-kpi-num b { margin-right: 2px; font-size: 24px; color: #0f172a; font-variant-numeric: tabular-nums; }
    .ve-kpi-estado { flex: none; display: inline-flex; align-items: center; gap: 5px; padding: 3px 12px; border-radius: 999px; font-size: 13px; font-weight: 700; white-space: nowrap; }
    .ve-kpi-ok .ve-kpi-estado { background: #e7f5ec; color: #166534; }
    .ve-kpi-mal { border-color: #fcd9a0; }
    .ve-kpi-mal .ve-kpi-estado { background: #fdf0d9; color: #7a4f0f; }
    /* Tarjeta como filtro: clic para ver solo ese cliente, otro clic para ver todos. */
    .ve-kpi[data-tarjeta] { cursor: pointer; transition: border-color .15s, box-shadow .15s, background-color .15s, opacity .15s; }
    /* Al pasar el mouse: solo una sombra (sin azul: azul es «elegida»). */
    .ve-kpi[data-tarjeta]:hover { box-shadow: 0 4px 14px rgba(15, 23, 42, 0.1); }
    .ve-kpi[data-tarjeta]:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
    .ve-kpi[aria-pressed='true'] { border: 2px solid #2563eb; padding: 13px 15px; background: #eff6ff; box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12); }
    /* Con una elegida, las demás quedan iguales: atenuadas, borde neutro, sin azul. */
    .ve-kpis[data-filtrando] .ve-kpi:not([aria-pressed='true']),
    .ve-kpis[data-filtrando] .ve-kpi:not([aria-pressed='true']):hover { opacity: .65; border-color: #e2e8f0; background: #fff; box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05); }
    .ve-kpis[data-filtrando] .ve-kpi:not([aria-pressed='true']):hover { opacity: .85; }
    .ve-error[hidden] { display: none; }
    .ve-filtro-tarjeta { display: inline-flex; align-items: center; gap: 8px; margin-left: auto; font-size: 13.5px; font-weight: 600; color: #1e3a8a; }
    .ve-filtro-tarjeta[hidden] { display: none; }
    .ve-barra[hidden], .ve-guia[hidden] { display: none; }
    /* Sin cliente elegido: en lugar de la lista, cómo seguir. */
    .ve-guia { margin: 4px 0 0; padding: 28px 16px; border: 1px dashed #cbd5e1; border-radius: 12px; background: #fff; color: #64748b; font-size: 15px; font-weight: 600; text-align: center; }
    .ve-limpiar { min-height: 34px; border: 1px solid #bfdbfe; background: #fff; color: #1d4ed8; font: inherit; font-size: 13.5px; font-weight: 700; padding: 4px 14px; border-radius: 999px; cursor: pointer; }
    .ve-limpiar:hover { background: #eff6ff; border-color: #2563eb; }
    .ve-limpiar:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }

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
    .ve-chip[hidden] { display: none; }
    /* «Error» solo aparece si hay: en rojo, para resolverlo primero. */
    .ve-chip-error { border-color: #fca5a5; background: #fef2f2; color: #b91c1c; }
    .ve-chip-error:hover:not(:disabled) { border-color: #dc2626; background: #fee2e2; }
    .ve-chip-error .ve-chip-num { background: #fee2e2; color: #b91c1c; }
    .ve-chip-error[aria-pressed='true'] { background: #dc2626; border-color: #dc2626; color: #fff; }
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
    /* Los botones de mail de una operación (dos en «Subir a Xubio»): separados igual. */
    .ve-acciones > [data-op-mail] { display: inline-flex; flex-wrap: wrap; gap: 8px; }
    .ve-acciones > [data-op-mail][hidden] { display: none; }
    /* «Subir a Xubio»: arriba lo que cuenta; abajo «Subir» a todo el ancho y, debajo,
       los dos mails al cliente lado a lado y del mismo ancho. */
    .ve-derecha-subir { flex-direction: column; align-items: stretch; flex-wrap: nowrap; gap: 8px; }
    .ve-metas { display: flex; justify-content: flex-end; gap: 10px; }
    .ve-acciones-subir { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .ve-acciones-subir > .ve-boton-chico { grid-column: 1 / -1; }
    .ve-acciones-subir > [data-op-mail] { display: contents; }
    .ve-acciones-subir [data-op-mail] .ve-boton { padding: 8px 14px; }
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
    /* «Columnas no suman»: los títulos de ARCA van enteros (en dos renglones si hace falta). */
    .ve-grilla table.ve-suma th { white-space: normal; vertical-align: bottom; line-height: 1.25; }
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
      .ve-metas { justify-content: flex-start; }
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
  const n = conteoDe(errores, cliente, operacion, hojas);
  return n.error + n.soloArca + n.soloXubio - n.explicadas;
}

/** Los bloques de un cliente y una operación. */
function conteoDe(errores: readonly Advertencia[], cliente: string, operacion: string, hojas?: HojasExcel): ConteoBloques {
  const clave = claveExcel(cliente, operacion);
  const solo = (h: FaltantesDelExcel | undefined) => (h?.get(clave) ? new Map([[clave, h.get(clave)!]]) : undefined);
  return contarBloques(
    errores.filter((e) => e.cliente === cliente && e.operacion === operacion),
    solo(hojas?.sinLibro),
    solo(hojas?.sinArca),
  );
}

const OPERACIONES: readonly ResumenOperacion['operacion'][] = ['Ventas', 'Compras'];

/** Los números de los filtros de estado (Todos | Error | Subir a Xubio | A revisar). */
type ConteoFiltros = Record<'todos' | 'error' | 'subir' | 'revisar', number>;

/**
 * Los números de los filtros de un cliente (o de todos, con `null`). Las sueltas (un
 * paso que falló, ARCA vacío…) cuentan donde se ven: los errores graves en «Error»,
 * «Las columnas no suman» en «A revisar». Así «Todos» es la suma de los otros tres.
 */
function conteoCliente(errores: readonly Advertencia[], hojas: HojasExcel | undefined, cliente: string | null): ConteoFiltros {
  const delCliente = (c: string) => !cliente || c === cliente;
  const hoja = (h: FaltantesDelExcel | undefined) => h && new Map([...h].filter(([clave]) => delCliente(clave.slice(0, clave.lastIndexOf('|')))));
  const propios = errores.filter((e) => delCliente(e.cliente));
  const n = contarBloques(propios, hoja(hojas?.sinLibro), hoja(hojas?.sinArca));
  const sueltas = propios.filter(esSuelta);
  const error = n.error + sueltas.filter((e) => verDeSuelta(e) === 'error').length;
  const revisar = n.soloXubio + sueltas.filter((e) => verDeSuelta(e) === 'revisar').length;
  return { todos: error + n.soloArca + revisar, error, subir: n.soloArca, revisar };
}

/**
 * La tira de KPIs: una tarjeta por cliente con cuánto hay que gestionar (Ventas y
 * Compras juntos) y, adentro, cuánto coincide en cada operación que se corrió.
 * `interactivo`: la tarjeta filtra la lista de abajo a ese cliente.
 */
function bloqueResumen(
  resumenes: readonly ResumenOperacion[],
  clientes: readonly string[],
  errores: readonly Advertencia[],
  hojas?: HojasExcel,
  interactivo = false,
): string {
  if (!resumenes.length) return '';
  // Un cliente con hallazgos pero sin resumen (falló antes de cruzar) también tiene tarjeta.
  const porCliente = new Map<string, ResumenOperacion[]>();
  for (const r of resumenes) porCliente.set(r.cliente, [...(porCliente.get(r.cliente) ?? []), r]);
  for (const c of clientes) if (!porCliente.has(c)) porCliente.set(c, []);
  const tarjetas = [...porCliente]
    .map(([cliente, ops]) => {
      const modulos = OPERACIONES.flatMap((op) => ops.filter((r) => r.operacion === op)).map((r) => {
        const pendientes = pendientesDe(errores, cliente, r.operacion, hojas);
        return {
          pendientes,
          html: `<span class="ve-kpi-mod${pendientes ? ' ve-kpi-mod-mal' : ''}">
            <span class="ve-kpi-op">${esc(r.operacion)}</span>
            <span class="ve-kpi-num"><b>${r.coinciden}</b> de ${r.arca} comprobantes de ARCA coinciden con Xubio</span>
          </span>`,
        };
      });
      // Los graves sueltos (un paso que falló…) también son pendientes.
      const sueltas = errores.filter((e) => e.cliente === cliente && esSuelta(e)).length;
      const pendientes = modulos.reduce((s, m) => s + m.pendientes, sueltas);
      const ok = pendientes === 0;
      const filtro = interactivo
        ? ` data-tarjeta="${esc(cliente)}" role="button" tabindex="0" aria-pressed="false" title="Ver los hallazgos de ${esc(cliente)}"`
        : '';
      const cuerpo = modulos.length ? modulos.map((m) => m.html).join('') : '<span class="ve-kpi-num">Sin resultado del cruce</span>';
      return `<li class="ve-kpi ${ok ? 've-kpi-ok' : 've-kpi-mal'}"${filtro}>
        <span class="ve-kpi-cab"><span class="ve-kpi-nombre" title="${esc(cliente)}">${esc(cliente)}</span><span class="ve-kpi-estado">${ok ? '✓ Sin pendientes' : `${pendientes} a gestionar`}</span></span>
        <span class="ve-kpi-mods">${cuerpo}</span>
      </li>`;
    })
    .join('');
  return `<ul class="ve-kpis" aria-label="Resumen por cliente">${tarjetas}</ul>`;
}

/**
 * La barra de filtros por estado (Todos | Error | Subir a Xubio | A revisar). Los
 * números y qué se ve los ajusta la ventana al abrir y con cada clic.
 */
function filtrosGlobales(n: ConteoFiltros): string {
  const chips: [string, string, number][] = [
    ['', 'Todos', n.todos],
    ['error', 'Error', n.error],
    ['subir', 'Subir a Xubio', n.subir],
    ['revisar', 'A revisar', n.revisar],
  ];
  return `<div class="ve-filtros" role="group" aria-label="Filtrar por estado">${chips
    .map(
      ([id, nombre, k]) =>
        `<button type="button" class="ve-chip${id === 'error' ? ' ve-chip-error' : ''}" data-ver="${id}" aria-pressed="${id === ''}">${nombre} <span class="ve-chip-num">${k}</span></button>`,
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
  /** Abrir con un cliente ya elegido (por ejemplo, desde su fila). */
  clienteInicial?: string,
) {
  contexto = ctx;
  mailsArmados.clear();
  adjuntos.clear();
  const hechas = leerHechas(ejecucionId);
  const graves = contarErrores(errores).grave;
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
  // Los números de los filtros: de todos y de cada cliente.
  const conteoTotal = conteoCliente(errores, hojas, null);
  const conteoPorCliente = new Map([...porCliente.keys()].map((c) => [c, conteoCliente(errores, hojas, c)] as const));
  // Con tarjetas, la lista es de a un cliente: no se mezclan empresas. Sin tarjetas
  // (la ventana de un solo hallazgo) se ve todo lo que vino.
  const conTarjetas = hayAlgo && resumenes.length > 0;
  const barra =
    hayAlgo && conteoTotal.todos > 0
      ? `<div class="ve-barra">${filtrosGlobales(conteoTotal)}${
          conTarjetas
            ? '<span class="ve-filtro-tarjeta" data-filtro-tarjeta hidden><b data-filtro-nombre></b><button type="button" class="ve-limpiar" data-limpiar>Ver otro cliente ✕</button></span>'
            : ''
        }</div>`
      : '';
  const guia = conTarjetas ? '<p class="ve-guia" data-guia>Seleccioná un cliente arriba para ver el detalle de sus hallazgos.</p>' : '';
  const vacio = '<p class="ve-guia" data-sin-hallazgos hidden>Este cliente no tiene hallazgos.</p>';
  // Elegido de entrada: el que pidieron, o el único que hay.
  const elegidoAlAbrir = clienteInicial && porCliente.has(clienteInicial) ? clienteInicial : porCliente.size === 1 ? [...porCliente.keys()][0] : null;
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
        ${
          sub || (hayAlgo && ctx.descargarHallazgos)
            ? `<div class="ve-cabeza">${sub ? `<p class="ve-sub">${sub}</p>` : '<span></span>'}${
                errores.length && ctx.descargarHallazgos
                  ? '<button type="button" class="ve-limpiar ve-descargar" data-descargar-hallazgos title="Baja los hallazgos en un Excel para guardarlos como registro">⬇ Descargar hallazgos (Excel)</button>'
                  : ''
              }</div>`
            : ''
        }
        ${
          graves
            ? `<div class="ve-grave-aviso" role="alert"><span class="ve-grave-icono" aria-hidden="true">!</span><span><strong>El control terminó con ${graves === 1 ? 'un error grave' : `${graves} errores graves`}.</strong> Hasta corregirlo, el resultado no es confiable.</span></div>`
            : ''
        }
        ${bloqueResumen(resumenes, [...porCliente.keys()], errores, hojas, conTarjetas)}
        ${guia}
        ${barra}
        ${vacio}
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
      // Filtros: el cliente (tarjeta del resumen) y el estado, combinados. Con tarjetas
      // y ninguna elegida no se ve la lista: solo la guía para elegir un cliente.
      const filtro: { cliente: string | null; ver: string } = { cliente: elegidoAlAbrir, ver: '' };
      const chips = popup.querySelectorAll<HTMLButtonElement>('button[data-ver]');
      const tarjetas = popup.querySelectorAll<HTMLElement>('[data-tarjeta]');
      const conteoActual = () => (filtro.cliente && conteoPorCliente.get(filtro.cliente)) || conteoTotal;
      /** Si hay errores en lo que se ve, «Error» va primero: se elige solo. */
      const priorizarError = () => {
        if (conteoActual().error > 0) filtro.ver = 'error';
      };
      const filtrar = () => {
        // Estado: «Error» desaparece en 0; los otros se deshabilitan.
        const n = conteoActual();
        for (const chip of chips) {
          const id = chip.dataset['ver'] ?? '';
          const k = n[(id || 'todos') as keyof ConteoFiltros] ?? 0;
          const num = chip.querySelector('.ve-chip-num');
          if (num) num.textContent = String(k);
          chip.disabled = !!id && !k;
          if (id === 'error') chip.hidden = !k;
        }
        if (filtro.ver && !n[filtro.ver as keyof ConteoFiltros]) filtro.ver = '';
        for (const chip of chips) chip.setAttribute('aria-pressed', String((chip.dataset['ver'] ?? '') === filtro.ver));
        const { cliente, ver } = filtro;
        const esperando = conTarjetas && !cliente;
        const guiaEl = popup.querySelector<HTMLElement>('[data-guia]');
        if (guiaEl) guiaEl.hidden = !esperando;
        const barraEl = popup.querySelector<HTMLElement>('.ve-barra');
        if (barraEl) barraEl.hidden = esperando;
        const sinHallazgos = popup.querySelector<HTMLElement>('[data-sin-hallazgos]');
        if (sinHallazgos) sinHallazgos.hidden = !cliente || !!conteoPorCliente.get(cliente)?.todos;
        // Tarjetas: la elegida resaltada, las otras atenuadas por igual (en reposo, todas normales).
        for (const t of tarjetas) t.setAttribute('aria-pressed', String(t.dataset['tarjeta'] === cliente));
        popup.querySelector('.ve-kpis')?.toggleAttribute('data-filtrando', !!cliente);
        const aviso = popup.querySelector<HTMLElement>('[data-filtro-tarjeta]');
        if (aviso) {
          aviso.hidden = !cliente;
          const nombre = aviso.querySelector('[data-filtro-nombre]');
          if (nombre) nombre.textContent = cliente ?? '';
        }
        for (const d of popup.querySelectorAll<HTMLElement>('.ve-grupo > details')) {
          d.hidden = !!ver && (d.dataset['bloque'] ?? d.dataset['suelta']) !== ver;
        }
        for (const grupo of popup.querySelectorAll<HTMLElement>('.ve-grupo')) {
          grupo.hidden =
            esperando || (!!cliente && grupo.dataset['cliente'] !== cliente) || (!!ver && !grupo.querySelector(':scope > details:not([hidden])'));
        }
      };
      grupoDeBotones(chips, 'aria-pressed', (b) => {
        filtro.ver = b.dataset['ver'] ?? '';
        filtrar();
      });
      const alternarTarjeta = (t: HTMLElement) => {
        filtro.cliente = filtro.cliente === t.dataset['tarjeta'] ? null : (t.dataset['tarjeta'] ?? null);
        if (filtro.cliente) priorizarError();
        filtrar();
      };
      for (const t of tarjetas) {
        t.addEventListener('click', () => alternarTarjeta(t));
        t.addEventListener('keydown', (ev) => {
          if (ev.key !== 'Enter' && ev.key !== ' ') return;
          ev.preventDefault();
          alternarTarjeta(t);
        });
      }
      popup.querySelector('[data-limpiar]')?.addEventListener('click', (ev) => {
        ev.preventDefault();
        filtro.cliente = null;
        filtrar();
      });
      // «Descargar hallazgos»: del cliente elegido arriba, o de todos.
      const botonDescargar = popup.querySelector<HTMLButtonElement>('[data-descargar-hallazgos]');
      const textoDescargar = () => {
        if (botonDescargar) botonDescargar.textContent = filtro.cliente || unSoloCliente ? '⬇ Descargar hallazgos (Excel)' : '⬇ Descargar hallazgos de todos (Excel)';
      };
      botonDescargar?.addEventListener('click', async (ev) => {
        ev.preventDefault();
        if (!contexto.descargarHallazgos) return;
        botonDescargar.disabled = true;
        try {
          await contexto.descargarHallazgos(filtro.cliente);
          avisoMail('Se descargó el Excel con los hallazgos.');
        } catch {
          avisoMail('No se pudo descargar el Excel de los hallazgos.', 'error');
        } finally {
          botonDescargar.disabled = false;
        }
      });
      for (const t of tarjetas) t.addEventListener('click', textoDescargar);
      popup.querySelector('[data-limpiar]')?.addEventListener('click', textoDescargar);
      textoDescargar();
      // Al abrir: si hay errores en lo que se ve, se empieza por ellos.
      if (!conTarjetas || filtro.cliente) priorizarError();
      filtrar();
      // Pills del bloque «Error»: Letra / Tipo / Importe…
      for (const pills of popup.querySelectorAll<HTMLElement>('.ve-pills')) {
        const bloque = pills.closest('.ve-bloque');
        grupoDeBotones(pills.querySelectorAll<HTMLButtonElement>('button[data-pill]'), 'aria-pressed', (b) => {
          const clase = b.dataset['pill'];
          for (const par of bloque?.querySelectorAll<HTMLElement>('tbody.ve-par') ?? []) par.hidden = !!clase && par.dataset['clase'] !== clase;
        });
      }
      // «A revisar» con Ventas y Compras: «Todos» (al abrir) o una operación, cada uno con su botón de mail.
      for (const pills of popup.querySelectorAll<HTMLElement>('[data-pills-op]')) {
        const bloque = pills.closest('.ve-bloque');
        const mostrar = (op: string) => {
          for (const t of bloque?.querySelectorAll<HTMLElement>('tbody[data-op]') ?? []) t.hidden = !!op && t.dataset['op'] !== op;
          for (const m of bloque?.querySelectorAll<HTMLElement>('[data-op-mail]') ?? []) m.hidden = m.dataset['opMail'] !== op;
        };
        const botones = pills.querySelectorAll<HTMLButtonElement>('button[data-op-pill]');
        grupoDeBotones(botones, 'aria-pressed', (b) => mostrar(b.dataset['opPill'] ?? ''));
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
      // «Consultar al cliente»: el mail con la tabla queda copiado mientras se abre Gmail.
      popup.addEventListener('click', (ev) => {
        const boton = (ev.target as HTMLElement).closest<HTMLElement>('a[data-mail]');
        const m = boton ? mailsArmados.get(boton.dataset['mail'] ?? '') : undefined;
        if (!boton || !m) return;
        ev.preventDefault();
        const adjunto = adjuntos.get(boton.dataset['mail'] ?? '');
        const descargar = adjunto && contexto.descargarExcelFaltantes;
        void abrirMail(m, contexto.pegarEnGmail, descargar ? ' El Excel de las partidas se descargó: adjuntalo al mail.' : '');
        if (descargar) {
          descargar(adjunto.cliente, adjunto.operacion).catch(() =>
            avisoMail('No se pudo descargar el Excel de las partidas para adjuntar.', 'error'),
          );
        }
      });
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

// --- «Descargar hallazgos»: lo mismo que la ventana, en filas para un Excel sin estilos ---

export type CeldaRegistro = string | number | null;

/** Un hallazgo como se ve en la ventana: insignia, título, qué hacer, detalle y su tabla. */
export interface BloqueRegistro {
  cliente: string;
  operacion: string;
  insignia: string;
  titulo: string;
  que_hacer: string;
  detalle: string;
  columnas: string[];
  filas: CeldaRegistro[][];
}

function numeroORaya(v: CeldaVista['valor'] | undefined): CeldaRegistro {
  if (v === null || v === undefined || v === '') return '-';
  return aNumero(v) ?? String(v);
}

function insigniaDe(e: Advertencia): string {
  if (e.tipo === 'total_no_cierra' && e.gravedad !== 'info') return 'A revisar';
  const clase = claseDe(e);
  return clase === 'grave' ? 'Grave' : clase === 'aviso' ? 'A revisar' : clase === 'info' ? 'Info' : 'Error';
}

/** Las filas de un hallazgo: «Letra distinta» trae ARCA y Xubio juntas. */
function filasRegistro(e: Advertencia): { origen: 'ARCA' | 'Xubio'; celdas: CeldaVista[]; nota: string }[] {
  return e.comprobantes.flatMap((c) =>
    'fila_arca' in c
      ? [
          { origen: 'ARCA' as const, celdas: c.fila_arca ?? [], nota: '' },
          { origen: 'Xubio' as const, celdas: c.fila_xubio ?? [], nota: '' },
        ]
      : [{ origen: c.origen === 'arca' ? ('ARCA' as const) : ('Xubio' as const), celdas: c.fila ?? [], nota: c.motivo ? (NOTA_MOTIVO[c.motivo]?.texto ?? '') : '' }],
  );
}

function tablaRegistro(e: Advertencia): { columnas: string[]; filas: CeldaRegistro[][] } {
  const filas = filasRegistro(e);
  if (!filas.length) return { columnas: [], filas: [] };
  if (e.tipo === 'total_no_cierra') {
    // Igual que la grilla de la ventana: cada alícuota y lo que suma, el total, la diferencia y Xubio.
    const partes = filas[0].celdas.map((c) => c.columna).filter((c) => COLUMNA_QUE_SUMA.test(c.trim()));
    return {
      columnas: ['Fecha', 'Comprobante', 'Razón social', ...partes, 'Imp. Total', 'Diferencia', 'En Xubio'],
      filas: filas.map(({ celdas }) => {
        const d = normalizar(celdas);
        const v: Record<string, CeldaVista['valor']> = {};
        for (const c of celdas) v[c.columna] = c.valor;
        const xubio = v['Total Xubio'];
        return [
          texto(d.Fecha), texto(d.Comprobante), texto(d['Razón social']),
          ...partes.map((p) => (v[p] ? numeroORaya(v[p]) : '-')),
          numeroORaya(d.Total), numeroORaya(v['Diferencia']),
          xubio === null || xubio === undefined ? 'No está' : numeroORaya(xubio),
        ];
      }),
    };
  }
  const conOrigen = new Set(filas.map((f) => f.origen)).size > 1;
  const conNota = filas.some((f) => f.nota);
  return {
    columnas: [...(conOrigen ? ['Origen'] : []), 'Fecha', 'Tipo', 'Comprobante', 'CUIT', 'Razón social', ...(conNota ? ['Observación'] : []), 'Total'],
    filas: filas.map((f) => {
      const d = normalizar(f.celdas);
      return [
        ...(conOrigen ? [f.origen] : []),
        texto(d.Fecha), abreviarTipo(texto(d.Tipo)), texto(d.Comprobante), cuit(d.CUIT), texto(d['Razón social']),
        ...(conNota ? [f.nota] : []),
        numeroORaya(d.Total),
      ];
    }),
  };
}

/** Los hallazgos (del cliente elegido o de todos) como se ven en la ventana, para el Excel. */
export function registroDeHallazgos(errores: readonly Advertencia[], cliente: string | null = null): BloqueRegistro[] {
  return ordenarHallazgos(errores.filter((e) => !cliente || e.cliente === cliente))
    .sort((a, b) => a.cliente.localeCompare(b.cliente))
    .map((e) => {
      const n = e.total;
      return {
        cliente: e.cliente,
        operacion: e.operacion ?? '',
        insignia: insigniaDe(e),
        titulo: `${e.titulo}${n ? ` (${plural(n, 'comprobante', 'comprobantes')})` : ''}`,
        que_hacer: ordenDe(e)?.orden ?? '',
        detalle: e.detalle,
        ...tablaRegistro(e),
      };
    });
}
