import { Advertencia } from '../../core/models';

/**
 * Los hallazgos del control traducidos a órdenes: qué hacer, dónde y con
 * cuántos comprobantes. La pantalla del control los muestra como una lista de
 * tareas para tildar; la ventana de hallazgos repite la orden en cada tarjeta.
 */

/** En qué orden conviene hacerlas: las partidas de ARCA que faltan en el Libro IVA, después lo que deja el control sin valor. */
export type GrupoTarea = 'prioridad' | 'primero' | 'xubio' | 'cliente';

export const GRUPOS: readonly { id: GrupoTarea; titulo: string; ayuda: string }[] = [
  { id: 'prioridad', titulo: 'Faltantes en IVA', ayuda: 'Están en ARCA y no en el Libro IVA: cargalos en Xubio.' },
  { id: 'primero', titulo: 'Primero', ayuda: 'Sin esto el control no sirve: corregilo y volvé a correrlo.' },
  { id: 'xubio', titulo: 'En Xubio', ayuda: 'Para que el Libro IVA quede igual que ARCA.' },
  { id: 'cliente', titulo: 'Con el cliente', ayuda: 'Lo que solo el cliente puede aclarar.' },
];

/** Adónde lleva el botón de la tarea. «reintentar» arma un control nuevo con ese cliente. */
export type Destino = { texto: string; ruta: string } | { texto: string; reintentar: true };

export interface Tarea {
  /** Estable entre recargas: sirve para recordar si ya se hizo. */
  clave: string;
  grupo: GrupoTarea;
  cliente: string;
  operacion: string | null;
  /** La orden, en imperativo: «Cambiá la letra de 2 comprobantes en Xubio». */
  orden: string;
  /** Por qué (lo que encontró el control). */
  porque: string;
  destino?: Destino;
  /** La advertencia de la que sale (para abrir su detalle). */
  advertencia: Advertencia;
}

function comprobantes(n: number): string {
  return `${n} comprobante${n === 1 ? '' : 's'}`;
}

/**
 * Cuántos hay que tocar de verdad. Los controles viejos no traen `a_corregir`:
 * se cuentan los que no son «normales» (tiques, extractos…) o, si la lista vino
 * recortada, el número con el que empieza el detalle («2 no están en el libro…»).
 */
/** Errores que llegan de a pares de filas (ARCA, Xubio) por cada comprobante. */
export function esDePares(a: Pick<Advertencia, 'tipo'>): boolean {
  return a.tipo === 'tipo_distinto' || a.tipo === 'cuit_distinto' || a.tipo === 'importe_distinto';
}

/** Cuántos comprobantes abarca el hallazgo: en los de a pares, uno por par (no por fila). */
export function cantidadComprobantes(a: Pick<Advertencia, 'tipo' | 'total'>): number {
  return esDePares(a) ? Math.ceil(a.total / 2) : a.total;
}

export function aCorregir(a: Advertencia): number {
  // «tipo_distinto» no trae a_corregir (o trae las filas): se cuentan los pares.
  if (esDePares(a)) return cantidadComprobantes(a);
  if (a.a_corregir !== undefined) return a.a_corregir;
  if (a.tipo !== 'faltante_en_xubio' && a.tipo !== 'faltante_en_arca') return a.total;
  if (a.comprobantes.length >= a.total) return (a.comprobantes as { motivo?: string }[]).filter((c) => !c.motivo).length;
  const n = /^(\d+)/.exec(a.detalle ?? '');
  return n ? Number(n[1]) : a.total;
}

const CLIENTES: Destino ={ texto: 'Ir a Clientes', ruta: '/clientes' };
const REINTENTAR: Destino = { texto: 'Volver a correr', reintentar: true };

/** La orden de una advertencia. `null` si es informativa (no hay nada que hacer). */
export function ordenDe(a: Advertencia): { grupo: GrupoTarea; orden: string; destino?: Destino } | null {
  if (a.gravedad === 'info') return null;
  const n = aCorregir(a);
  switch (a.tipo) {
    case 'cuit_distinto':
      return { grupo: 'xubio', orden: `Corregí el CUIT (proveedor) de ${comprobantes(n)} en Xubio` };
    case 'importe_distinto':
      return { grupo: 'xubio', orden: `Corregí el importe de ${comprobantes(n)} en Xubio` };
    case 'total_no_cierra':
      return { grupo: 'cliente', orden: `Consultale al cliente la diferencia de ${comprobantes(n)} cuyas columnas en ARCA no suman el total` };
    case 'letra_distinta':
      return { grupo: 'xubio', orden: `Cambiá la letra de ${comprobantes(n)} en Xubio` };
    case 'tipo_distinto':
      return { grupo: 'xubio', orden: `Cambiá el tipo de ${comprobantes(n)} en Xubio` };
    case 'duplicado':
      return { grupo: 'xubio', orden: n === 1 ? 'Borrá la copia repetida en Xubio' : `Borrá las ${n} copias repetidas en Xubio` };
    case 'faltante_en_xubio':
      return {
        grupo: 'prioridad',
        orden: `Cargá en Xubio ${n === 1 ? 'el comprobante que falta' : `los ${n} comprobantes que faltan`}`,
        destino: { texto: 'Ir a Faltantes a Xubio', ruta: '/convertir-aux' },
      };
    case 'faltante_en_arca':
      return { grupo: 'cliente', orden: `Preguntale al cliente por ${comprobantes(n)} que están en Xubio y no en ARCA` };
    case 'arca_vacio':
      return { grupo: 'primero', orden: 'Revisá el CUIT representado del cliente y volvé a correr el control', destino: CLIENTES };
    case 'xubio_vacio':
      return { grupo: 'primero', orden: 'Revisá que la empresa en Xubio se llame igual que el cliente y volvé a correrlo', destino: REINTENTAR };
    case 'paso_error': {
      const accion = (a.acciones[0] ?? '').toLowerCase();
      const grupo: GrupoTarea = a.gravedad === 'grave' ? 'primero' : 'xubio';
      if (accion.includes('clave de arca')) return { grupo, orden: 'Corregí el CUIT de ingreso o la clave fiscal de ARCA y volvé a correrlo', destino: CLIENTES };
      if (accion.includes('clave de xubio')) return { grupo, orden: 'Revisá el usuario y la clave de Xubio y volvé a correrlo', destino: REINTENTAR };
      if (accion.includes('empresa')) return { grupo, orden: 'Revisá que la empresa exista en Xubio con el mismo nombre y volvé a correrlo', destino: REINTENTAR };
      return { grupo, orden: `Volvé a correr el control (${a.titulo.replace(/:.*$/, '')})`, destino: REINTENTAR };
    }
    default:
      return { grupo: 'xubio', orden: a.titulo };
  }
}

export function tareasDe(advertencias: readonly Advertencia[]): Tarea[] {
  const orden: Record<GrupoTarea, number> = { prioridad: 0, primero: 1, xubio: 2, cliente: 3 };
  const tareas: Tarea[] = [];
  for (const a of advertencias) {
    const o = ordenDe(a);
    if (!o) continue;
    tareas.push({
      clave: [a.cliente, a.operacion ?? '', a.tipo, a.titulo].join('|'),
      grupo: o.grupo,
      cliente: a.cliente,
      operacion: a.operacion,
      orden: o.orden,
      porque: a.detalle || a.titulo,
      destino: o.destino,
      advertencia: a,
    });
  }
  // Las partidas de ARCA no encontradas en el Libro IVA van siempre arriba (grupo «Prioridad»).
  return tareas.sort((x, y) => orden[x.grupo] - orden[y.grupo]);
}

// --- Tildadas: quedan guardadas en este navegador, por control -----------------------

const PREFIJO = 'cf-tareas-hechas:';

export function leerHechas(ejecucion: string): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(PREFIJO + ejecucion) ?? '[]') as string[]);
  } catch {
    return new Set();
  }
}

export function guardarHechas(ejecucion: string, hechas: ReadonlySet<string>): void {
  try {
    localStorage.setItem(PREFIJO + ejecucion, JSON.stringify([...hechas]));
  } catch {
    // Sin localStorage las tildes duran hasta recargar la página.
  }
}
