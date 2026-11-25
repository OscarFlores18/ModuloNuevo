import { signal } from '@angular/core';
import { escribirTexto, leerTexto } from '../../core/almacen';

/**
 * Qué se sube a Xubio desde un control, elegido a mano fila por fila:
 *
 * - «Subir a Xubio»: cada partida de ARCA no encontrada en el Libro IVA lleva un
 *   checkbox (tildadas por defecto). Las destildadas no van al .zip.
 * - «Error»: cada par ARCA/Xubio (letra, tipo, importe, CUIT) se tilda para
 *   corregirlo con el dato de ARCA. Tildar solo lo elige: el cambio se hace con
 *   «Aplicar cambios», que corrige el Libro IVA en el servidor y vuelve a cruzar.
 *
 * Queda en este navegador, por control. La leen la ventana de hallazgos, la lista
 * de «Qué hay que hacer» y «Faltantes a Xubio» (paso 2).
 */
interface Guardado {
  /** «cliente|operación|CUIT|comprobante» de las filas destildadas. */
  excluidas: string[];
  /** Pares de «Error» tildados para «Aplicar cambios» (clave de `claveCambio`). */
  aplicar: string[];
  /** Filas que ya se destildaron solas una vez (liquidaciones bancarias): si se vuelven a tildar, se respeta. */
  automaticas: string[];
}

const PREFIJO = 'cf-seleccion-xubio:';
/** Sin control (no debería pasar) la elección dura lo que la página. */
const enMemoria = new Map<string, Guardado>();
const version = signal(0);

/** Cambia con cada tilde: para que las pantallas de Angular se enteren. */
export const cambiosSeleccion = version.asReadonly();

function leer(ejecucionId: string | undefined): Guardado {
  const clave = ejecucionId ?? '';
  const cache = enMemoria.get(clave);
  if (cache) return cache;
  let g: Guardado = { excluidas: [], aplicar: [], automaticas: [] };
  if (ejecucionId) {
    try {
      const crudo = JSON.parse(leerTexto('local', PREFIJO + ejecucionId) ?? 'null') as Partial<Guardado> | null;
      if (crudo)
        g = {
          excluidas: Array.isArray(crudo.excluidas) ? crudo.excluidas : [],
          aplicar: Array.isArray(crudo.aplicar) ? crudo.aplicar : [],
          automaticas: Array.isArray(crudo.automaticas) ? crudo.automaticas : [],
        };
    } catch {
      // Lo guardado no se entiende: se arranca de cero (todo tildado).
    }
  }
  enMemoria.set(clave, g);
  return g;
}

function guardar(ejecucionId: string | undefined, g: Guardado): void {
  enMemoria.set(ejecucionId ?? '', g);
  if (ejecucionId) escribirTexto('local', PREFIJO + ejecucionId, JSON.stringify(g));
  version.update((v) => v + 1);
}

/** «30500000000|A-00014-01481153»: la clave de una partida (la misma que usa el backend). */
export function claveFila(cuit: unknown, comprobante: unknown): string {
  return `${String(cuit ?? '').replace(/\D/g, '')}|${String(comprobante ?? '').trim()}`;
}

function completa(cliente: string, operacion: string, fila: string): string {
  return `${cliente}|${operacion}|${fila}`;
}

export function estaIncluida(ejecucionId: string | undefined, cliente: string, operacion: string, fila: string): boolean {
  version();
  return !leer(ejecucionId).excluidas.includes(completa(cliente, operacion, fila));
}

export function incluir(ejecucionId: string | undefined, cliente: string, operacion: string, fila: string, si: boolean): void {
  const g = leer(ejecucionId);
  const clave = completa(cliente, operacion, fila);
  const excluidas = g.excluidas.filter((k) => k !== clave);
  if (!si) excluidas.push(clave);
  guardar(ejecucionId, { ...g, excluidas });
}

/**
 * Destilda estas filas la primera vez que se ven (las liquidaciones bancarias, que
 * ya están en Xubio dentro del extracto). Si después alguien las tilda, quedan tildadas.
 */
export function destildarPorDefecto(ejecucionId: string | undefined, cliente: string, operacion: string, filas: readonly string[]): void {
  const g = leer(ejecucionId);
  const nuevas = filas.map((f) => completa(cliente, operacion, f)).filter((k) => !g.automaticas.includes(k));
  if (!nuevas.length) return;
  guardar(ejecucionId, {
    ...g,
    excluidas: [...g.excluidas, ...nuevas.filter((k) => !g.excluidas.includes(k))],
    automaticas: [...g.automaticas, ...nuevas],
  });
}

/** Las claves «CUIT|comprobante» destildadas de un cliente y operación (lo que no va al .zip). */
export function excluidasDe(ejecucionId: string | undefined, cliente: string, operacion: string): string[] {
  version();
  const prefijo = `${cliente}|${operacion}|`;
  return leer(ejecucionId)
    .excluidas.filter((k) => k.startsWith(prefijo))
    .map((k) => k.slice(prefijo.length));
}

/** La clave de un par de «Error» (cliente, operación, clase y la fila de ARCA). */
export function claveCambio(cliente: string, operacion: string, clase: string, filaArca: string): string {
  return `${cliente}|${operacion}|${clase}|${filaArca}`;
}

/** Tildado para «Aplicar cambios» (de entrada, ninguno). */
export function paraAplicar(ejecucionId: string | undefined, clave: string): boolean {
  version();
  return leer(ejecucionId).aplicar.includes(clave);
}

export function elegirParaAplicar(ejecucionId: string | undefined, claves: readonly string[], si: boolean): void {
  const g = leer(ejecucionId);
  const resto = g.aplicar.filter((k) => !claves.includes(k));
  guardar(ejecucionId, { ...g, aplicar: si ? [...resto, ...claves] : resto });
}

// --- Lo que viaja de «Ejecuciones» a «Faltantes a Xubio» ------------------------------

/** Una corrección ya aplicada en el Excel («Aplicar cambios»), para hacer también en Xubio. */
export interface CorreccionElegida {
  comprobante: string;
  razon: string;
  /** «Importe: $117.149,10 → $116.450,40». */
  texto: string;
}

/** Una partida que va al .zip (para mostrarla en el paso 2). */
export interface PartidaElegida {
  fecha: string;
  tipo: string;
  comprobante: string;
  cuit: string;
  razon: string;
  total: string;
}

/** «↑ Subir a Xubio» desde un control: lo que el paso 2 de «Faltantes a Xubio» precarga. */
export interface Precarga {
  ejecucionId: string;
  /** Posición del cliente en el control (para bajar su AUX). */
  indice: number;
  cliente: string;
  operacion: 'Ventas' | 'Compras';
  /** El AUX del control para esa operación. */
  aux: string;
  /** «CUIT|comprobante» destildadas: no van al .zip. */
  excluir: string[];
  partidas: PartidaElegida[];
  /** Cuántas tenía la hoja (para «3 de 4»). */
  total: number;
  correcciones: CorreccionElegida[];
}

const CLAVE_PRECARGA = 'cf-precarga-xubio';

export function guardarPrecarga(p: Precarga): void {
  escribirTexto('session', CLAVE_PRECARGA, JSON.stringify(p));
}

export function leerPrecarga(): Precarga | null {
  try {
    const p = JSON.parse(leerTexto('session', CLAVE_PRECARGA) ?? 'null') as Precarga | null;
    return p && typeof p.ejecucionId === 'string' && typeof p.aux === 'string' && Array.isArray(p.excluir) ? p : null;
  } catch {
    return null;
  }
}

export function borrarPrecarga(): void {
  escribirTexto('session', CLAVE_PRECARGA, null);
}
