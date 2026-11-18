import { ClienteEjecucion } from '../models';

export type Operacion = 'Ventas' | 'Compras';

/**
 * El AUX que arma el control para una operación («AUX - Control de facturas Compras con AFIP - …xlsx»).
 * «⬆ Subir a Xubio» desde un control lo pasa, sin los comprobantes destildados, a
 * «Faltantes a Xubio» (ver `ejecucion/seleccion.ts`).
 */
export function auxDe(cliente: Pick<ClienteEjecucion, 'archivos'>, operacion: Operacion): string | null {
  const patron = new RegExp(`^AUX - Control de facturas ${operacion}\\b.*\\.xlsx?$`, 'i');
  const todos = cliente.archivos.filter((a) => patron.test(a.nombre)).map((a) => a.nombre);
  // Después de «Aplicar cambios» vale el re-chequeado.
  return todos.find((n) => / - corregido\.xlsx?$/i.test(n)) ?? todos[0] ?? null;
}
