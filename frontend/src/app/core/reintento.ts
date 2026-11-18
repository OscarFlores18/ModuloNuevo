import { DetalleEjecucion } from './models';

/**
 * «Reintentar los que fallaron»: lo que viaja de Ejecuciones a Nuevo control
 * (estado de la navegación) para armar el mismo control solo con esos clientes.
 * Nuevo control lo carga en el formulario y valida todo como siempre: la clave
 * de Xubio, el período y los datos de cada cliente.
 */
export interface Reintento {
  clientes: string[];
  /** DD/MM/AAAA, como los guarda el servidor. */
  desde: string;
  hasta: string;
  ventas: boolean;
  compras: boolean;
  /** Para el aviso: de qué control viene. */
  creada: string;
}

/** Un cliente se reintenta si algún paso falló o si no llegó a correr (cancelado). */
export function clientesParaReintentar(e: DetalleEjecucion): string[] {
  return e.detalle_clientes
    .filter((c) => c.estado === 'cancelado' || c.estado === 'pendiente' || c.pasos.some((p) => p.estado === 'error'))
    .map((c) => c.nombre);
}

export function reintentoDe(e: DetalleEjecucion): Reintento | null {
  const clientes = clientesParaReintentar(e);
  if (!clientes.length) return null;
  const p = e.parametros;
  return { clientes, desde: p.desde, hasta: p.hasta, ventas: p.ventas, compras: p.compras, creada: e.creada };
}

/** Lo que llegó en el estado de la navegación, si es un reintento válido. */
export function leerReintento(estado: unknown): Reintento | null {
  const r = (estado as { reintento?: Reintento } | null)?.reintento;
  const fecha = /^\d{2}\/\d{2}\/\d{4}$/;
  if (!r || !Array.isArray(r.clientes) || !r.clientes.length || !fecha.test(r.desde) || !fecha.test(r.hasta)) return null;
  return r;
}
