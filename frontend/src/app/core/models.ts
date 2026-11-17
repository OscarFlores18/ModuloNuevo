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

/** Cuántos controles hubo de una operación en el período y cuál sería el próximo. */
export interface ResumenControl {
  realizados: number;
  proximo: number;
  /** «1er control», «2do control»… */
  proximo_texto: string;
  /** Fecha ISO del último AUX, o null si nunca se controló. */
  ultimo: string | null;
}

/** Por nombre de cliente. */
export type ControlesPorCliente = Record<string, { ventas: ResumenControl; compras: ResumenControl }>;

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
  /** Link de Xubio ya parado en la empresa del cliente (lo guarda el bot). */
  xubio_url?: string | null;
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
  /** El control espera una respuesta: `vence` en segundos epoch. */
  pausa: Pausa | null;
  /** Avisos que no frenan el control; se muestran al terminar. */
  advertencias?: Advertencia[];
  /** Por cliente y operación: cuántos comprobantes de ARCA tienen su par en Xubio. */
  resumenes?: ResumenOperacion[];
  detalle_clientes: ClienteEjecucion[];
  log: LineaLog[];
  log_total: number;
}

export interface ResumenOperacion {
  cliente: string;
  operacion: 'Ventas' | 'Compras';
  arca: number;
  xubio: number;
  coinciden: number;
  /** Hallazgos graves o a corregir de esa operación (0 = sin errores). */
  a_corregir: number;
}

/** Una celda de una fila de Excel tal como la manda el backend. */
export interface CeldaVista {
  columna: string;
  valor: string | number | null;
}

/** Un comprobante que en Xubio quedó con otra letra que en ARCA. */
export interface ComprobanteLetraDistinta {
  proveedor: string;
  cuit: string;
  comprobante_arca: string;
  comprobante_xubio: string;
  letra_arca: string;
  letra_xubio: string;
  /** La fila en «Mis Comprobantes» (ARCA) y en el Libro IVA (Xubio). */
  fila_arca: CeldaVista[];
  fila_xubio: CeldaVista[];
}

/** Una fila de ARCA o de Xubio que forma parte de un error. */
export interface FilaConError {
  origen: 'arca' | 'xubio';
  fila: CeldaVista[];
  /** Partida sin ARCA que es normal: tique o extracto bancario, o proveedor del exterior. */
  motivo?: 'tique' | 'exterior' | 'anulada' | 'liquidacion';
}

export type TipoError =
  | 'arca_vacio'
  | 'xubio_vacio'
  | 'letra_distinta'
  | 'faltante_en_xubio'
  | 'faltante_en_arca'
  | 'duplicado'
  | 'tique_sin_arca'
  | 'liquidacion_bancaria'
  | 'anulada_con_nc'
  | 'exterior_sin_arca'
  | 'tipo_distinto'
  | 'paso_error';

/** Un error del control (ventana de errores), con cómo solucionarlo. */
export interface Advertencia {
  tipo: TipoError;
  /** grave: el control no sirve · corregir: hay que cargar bien en Xubio · info: no hace falta hacer nada. */
  gravedad: 'grave' | 'corregir' | 'info';
  cliente: string;
  operacion: 'Ventas' | 'Compras' | null;
  titulo: string;
  detalle: string;
  /** Qué hacer, en etiquetas cortas («Corregir en Xubio», «Consultar al cliente»). */
  acciones: string[];
  /** Cuántos comprobantes hay en total (en `comprobantes` llegan hasta 10). */
  total: number;
  comprobantes: (ComprobanteLetraDistinta | FilaConError)[];
  /** La hoja del Excel de la auditoría donde se ven todos. */
  hoja: string | null;
}

/** Pausa entre un cliente y el siguiente (cuenta regresiva). */
export interface Pausa {
  cliente: string;
  vence: number;
  segundos: number;
}

export type AccionPausa = 'continuar' | 'cancelar' | 'no_preguntar';

export function estaActiva(estado: EstadoEjecucion | undefined | null): boolean {
  return estado === 'en_curso' || estado === 'esperando';
}

/** El mensaje de error que manda la API ({detail: {mensaje}} o {detail: "..."}). */
/**
 * Un error de la API ya traducido: `message` es lo que se le muestra al contador
 * y `tecnico` el detalle (código HTTP, respuesta del backend) para reportarlo.
 */
export class ErrorApi extends Error {
  constructor(
    mensaje: string,
    readonly tecnico: string,
    readonly estado: number,
  ) {
    super(mensaje);
    this.name = 'ErrorApi';
  }
}

export function mensajeDeError(e: unknown, porDefecto = 'Algo falló. Probá de nuevo.'): string {
  if (e instanceof ErrorApi) return e.message;
  const err = e as { status?: number; error?: { detail?: unknown } };
  if (err?.status === 0) return 'No se pudo hablar con el servidor. Revisá que el backend esté corriendo.';
  const d = err?.error?.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg).filter(Boolean).join(' · ') || porDefecto;
  if (d && typeof d === 'object' && 'mensaje' in d) return String((d as { mensaje: string }).mensaje);
  return porDefecto;
}
