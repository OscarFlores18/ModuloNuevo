import { EstadoCliente, EstadoEjecucion, PasoEjecucion, PasoId } from '../../core/models';

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
  finalizada: { texto: 'Finalizado', clase: 'mf-badge mf-badge-ok' },
  cancelada: { texto: 'Cancelado', clase: 'mf-badge mf-badge-tenue' },
  error: { texto: 'Error', clase: 'mf-badge mf-badge-error' },
};

export const INSIGNIA_CLIENTE: Record<EstadoCliente, Insignia> = {
  pendiente: { texto: 'En espera', clase: 'mf-badge' },
  en_curso: { texto: 'Procesando', clase: 'mf-badge mf-badge-acento', latido: true },
  ok: { texto: 'Listo', clase: 'mf-badge mf-badge-ok' },
  con_errores: { texto: 'Con errores', clase: 'mf-badge mf-badge-error' },
  cancelado: { texto: 'Cancelado', clase: 'mf-badge mf-badge-tenue' },
};

export const ETIQUETA_ARCHIVO: Record<string, string> = {
  arca: 'ARCA',
  xubio: 'Xubio',
  auditoria: 'Auditoría',
  otro: 'Archivo',
};

// --- Línea de tiempo: ARCA -> Xubio -> Auditoría ---------------------------------------

/**
 * El estado de cada etapa de la línea de tiempo. El backend manda un paso por
 * operación (`arca_ventas`, `arca_compras`, `xubio`, `auditoria_ventas`…): acá
 * se resumen en las tres etapas que ve el analista.
 */
export type EstadoEtapa = 'pendiente' | 'procesando' | 'ok' | 'aviso' | 'error' | 'omitido';

export interface Etapa {
  id: PasoId;
  titulo: string;
  estado: EstadoEtapa;
}

export const TEXTO_ETAPA: Record<EstadoEtapa, string> = {
  pendiente: 'Pendiente',
  procesando: 'Procesando',
  ok: 'OK',
  aviso: 'Con avisos',
  error: 'Error',
  omitido: 'No se hizo',
};

const ETAPAS: readonly { id: PasoId; titulo: string }[] = [
  { id: 'arca', titulo: 'ARCA' },
  { id: 'xubio', titulo: 'Xubio' },
  { id: 'auditoria', titulo: 'Auditoría' },
];

function estadoDeEtapa(pasos: readonly PasoEjecucion[]): EstadoEtapa {
  if (!pasos.length || pasos.every((p) => p.estado === 'omitido')) return 'omitido';
  if (pasos.some((p) => p.estado === 'error')) return 'error';
  if (pasos.some((p) => p.estado === 'en_curso')) return 'procesando';
  const pendientes = pasos.filter((p) => p.estado === 'pendiente').length;
  if (pendientes === pasos.length) return 'pendiente';
  // Entre Ventas y Compras de la misma etapa: sigue en marcha.
  if (pendientes > 0) return 'procesando';
  return pasos.some((p) => p.estado === 'aviso') ? 'aviso' : 'ok';
}

export function etapasDe(pasos: readonly PasoEjecucion[]): Etapa[] {
  return ETAPAS.map(({ id, titulo }) => ({
    id,
    titulo,
    estado: estadoDeEtapa(pasos.filter((p) => p.id === id || p.id.startsWith(`${id}_`))),
  }));
}

/** Avance de 0 a 100: un paso en curso cuenta la mitad. */
export function avanceDe(pasos: readonly PasoEjecucion[]): number {
  if (!pasos.length) return 0;
  const hechos = pasos.filter((p) => p.estado !== 'pendiente' && p.estado !== 'en_curso').length;
  const enCurso = pasos.filter((p) => p.estado === 'en_curso').length * 0.5;
  return Math.round(((hechos + enCurso) / pasos.length) * 100);
}
