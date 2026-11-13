import { EstadoCliente, EstadoEjecucion, EstadoPaso } from '../../core/models';

/** Colores y textos de cada estado: la pantalla solo pinta lo que sale de acá. */

export interface Insignia {
  texto: string;
  clase: string;
  punto: string;
}

export const INSIGNIA_EJECUCION: Record<EstadoEjecucion, Insignia> = {
  en_curso: { texto: 'En curso', clase: 'bg-brand-50 text-brand-700 ring-brand-100', punto: 'bg-brand-600 motion-safe:animate-pulse' },
  esperando: { texto: 'Esperando tu respuesta', clase: 'bg-amber-50 text-amber-800 ring-amber-200', punto: 'bg-amber-500 motion-safe:animate-pulse' },
  finalizada: { texto: 'Finalizada', clase: 'bg-emerald-50 text-emerald-700 ring-emerald-200', punto: 'bg-emerald-500' },
  cancelada: { texto: 'Cancelada', clase: 'bg-slate-100 text-slate-600 ring-slate-200', punto: 'bg-slate-400' },
  error: { texto: 'Se cortó', clase: 'bg-red-50 text-red-700 ring-red-200', punto: 'bg-red-500' },
};

export const INSIGNIA_CLIENTE: Record<EstadoCliente, Insignia> = {
  pendiente: { texto: 'En espera', clase: 'bg-slate-100 text-slate-600 ring-slate-200', punto: 'bg-slate-400' },
  en_curso: { texto: 'Procesando', clase: 'bg-brand-50 text-brand-700 ring-brand-100', punto: 'bg-brand-600 motion-safe:animate-pulse' },
  ok: { texto: 'Listo', clase: 'bg-emerald-50 text-emerald-700 ring-emerald-200', punto: 'bg-emerald-500' },
  con_errores: { texto: 'Con errores', clase: 'bg-red-50 text-red-700 ring-red-200', punto: 'bg-red-500' },
  cancelado: { texto: 'Cancelado', clase: 'bg-slate-100 text-slate-600 ring-slate-200', punto: 'bg-slate-400' },
};

export const ESTILO_PASO: Record<EstadoPaso, { caja: string; texto: string }> = {
  pendiente: { caja: 'border-line bg-white text-muted', texto: 'Pendiente' },
  en_curso: { caja: 'border-brand-300 bg-brand-50 text-brand-800 ring-1 ring-brand-200', texto: 'En curso' },
  ok: { caja: 'border-emerald-200 bg-emerald-50 text-emerald-800', texto: 'Listo' },
  aviso: { caja: 'border-amber-200 bg-amber-50 text-amber-900', texto: 'Terminó con avisos' },
  error: { caja: 'border-red-200 bg-red-50 text-red-800', texto: 'Error' },
  omitido: { caja: 'border-dashed border-line bg-canvas text-muted', texto: 'No se hizo' },
};

export const ETIQUETA_ARCHIVO: Record<string, string> = {
  arca: 'ARCA',
  xubio: 'Xubio',
  auditoria: 'Control',
  otro: 'Archivo',
};
