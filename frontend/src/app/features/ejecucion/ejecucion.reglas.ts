import { EstadoCliente, EstadoEjecucion, EstadoPaso } from '../../core/models';

/** Textos y badges (`mf-badge-*`) de cada estado: la pantalla solo pinta lo que sale de acá. */

export interface Insignia {
  texto: string;
  clase: string;
  /** El punto late mientras algo está pasando. */
  latido?: boolean;
}

export const INSIGNIA_EJECUCION: Record<EstadoEjecucion, Insignia> = {
  en_curso: { texto: 'En curso', clase: 'mf-badge mf-badge-acento', latido: true },
  esperando: { texto: 'Esperando tu respuesta', clase: 'mf-badge mf-badge-aviso', latido: true },
  finalizada: { texto: 'Finalizada', clase: 'mf-badge mf-badge-ok' },
  cancelada: { texto: 'Cancelada', clase: 'mf-badge mf-badge-tenue' },
  error: { texto: 'Se cortó', clase: 'mf-badge mf-badge-error' },
};

export const INSIGNIA_CLIENTE: Record<EstadoCliente, Insignia> = {
  pendiente: { texto: 'En espera', clase: 'mf-badge' },
  en_curso: { texto: 'Procesando', clase: 'mf-badge mf-badge-acento', latido: true },
  ok: { texto: 'Listo', clase: 'mf-badge mf-badge-ok' },
  con_errores: { texto: 'Con errores', clase: 'mf-badge mf-badge-error' },
  cancelado: { texto: 'Cancelado', clase: 'mf-badge mf-badge-tenue' },
};

export const INSIGNIA_PASO: Record<EstadoPaso, Insignia & { icono: string }> = {
  pendiente: { texto: 'Pendiente', clase: 'mf-badge', icono: '·' },
  en_curso: { texto: 'En curso', clase: 'mf-badge mf-badge-acento', icono: '…', latido: true },
  ok: { texto: 'Listo', clase: 'mf-badge mf-badge-ok', icono: '✓' },
  aviso: { texto: 'Con avisos', clase: 'mf-badge mf-badge-aviso', icono: '!' },
  error: { texto: 'Error', clase: 'mf-badge mf-badge-error', icono: '✕' },
  omitido: { texto: 'No se hizo', clase: 'mf-badge mf-badge-tenue', icono: '–' },
};

export const ETIQUETA_ARCHIVO: Record<string, string> = {
  arca: 'ARCA',
  xubio: 'Xubio',
  auditoria: 'Control',
  otro: 'Archivo',
};
