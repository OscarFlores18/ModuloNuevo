/** Lo que devuelve la API (backend/app/api/rutas.py). */

export type Rol = 'empleado' | 'supervisor';

export interface Usuario {
  id: string;
  nombre: string;
  roles: Rol[];
  nivel?: string;
  nivel_nombre?: string;
  estudio_nombre?: string;
  conectado?: boolean;
}

export interface Cliente {
  nombre: string;
  arca: { cuit_login: string; cuit_representado: string; tiene_clave: boolean };
  /** Datos que le faltan para correr todos los pasos («Clave fiscal», «CUIT representado»…). */
  faltantes: string[];
}

export interface ClienteEntrada {
  nombre: string;
  arca: { cuit_login: string; clave: string; cuit_representado: string };
}

export interface Ajustes {
  carpeta_salida: string;
  pausa_segundos: number;
  /** true: el backend corre en esta PC y guarda con ventanas de Windows. false (servidor): se baja por el navegador. */
  dialogos_locales: boolean;
  xubio: { usuario: string; tiene_clave: boolean; recordar: boolean; del_servidor: boolean };
}

export type PasoId = 'arca' | 'xubio' | 'auditoria';

export interface PedidoEjecucion {
  clientes: string[];
  desde: string; // DD/MM/AAAA
  hasta: string;
  ventas: boolean;
  compras: boolean;
  pasos: Record<PasoId, boolean>;
  carpeta: string;
  xubio: { usuario: string; clave: string; recordar: boolean };
}

export type EstadoEjecucion = 'en_curso' | 'esperando' | 'finalizada' | 'cancelada' | 'error';
export type EstadoCliente = 'pendiente' | 'en_curso' | 'ok' | 'con_errores' | 'cancelado';
export type EstadoPaso = 'pendiente' | 'en_curso' | 'ok' | 'aviso' | 'error' | 'omitido';

export interface ResumenEjecucion {
  id: string;
  usuario: string;
  estado: EstadoEjecucion;
  creada: string;
  terminada: string | null;
  mensaje: string;
  periodo: string;
  clientes: number;
  totales: { ok: number; con_errores: number; pendientes: number };
}

export interface PasoEjecucion {
  id: string;
  etiqueta: string;
  estado: EstadoPaso;
  mensaje: string;
  inicio: string | null;
  fin: string | null;
}

export interface ArchivoGenerado {
  nombre: string;
  tamano: number;
  modificado: string;
  tipo: 'arca' | 'xubio' | 'auditoria' | 'otro';
}

export interface ClienteEjecucion {
  nombre: string;
  estado: EstadoCliente;
  carpeta: string;
  archivos: ArchivoGenerado[];
  pasos: PasoEjecucion[];
}

export interface LineaLog {
  cuando: string;
  texto: string;
  tipo: 'info' | 'ok' | 'aviso' | 'error';
}

export interface DetalleEjecucion extends ResumenEjecucion {
  parametros: {
    desde: string;
    hasta: string;
    ventas: boolean;
    compras: boolean;
    pasos: Record<PasoId, boolean>;
    carpeta: string;
    xubio_usuario: string;
  };
  cliente_actual: number;
  preguntar: boolean;
  cancelar_pedido: boolean;
  /** La pausa entre clientes: `vence` en segundos epoch. */
  pausa: { cliente: string; vence: number; segundos: number } | null;
  detalle_clientes: ClienteEjecucion[];
  log: LineaLog[];
  log_total: number;
}

export function estaActiva(estado: EstadoEjecucion | undefined | null): boolean {
  return estado === 'en_curso' || estado === 'esperando';
}

/** El mensaje de error que manda la API ({detail: {mensaje}} o {detail: "..."}). */
export function mensajeDeError(e: unknown, porDefecto = 'Algo falló. Probá de nuevo.'): string {
  const err = e as { status?: number; error?: { detail?: unknown } };
  if (err?.status === 0) return 'No se pudo hablar con el servidor. Revisá que el backend esté corriendo.';
  const d = err?.error?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg).filter(Boolean).join(' · ') || porDefecto;
  if (d && typeof d === 'object' && 'mensaje' in d) return String((d as { mensaje: string }).mensaje);
  return porDefecto;
}
