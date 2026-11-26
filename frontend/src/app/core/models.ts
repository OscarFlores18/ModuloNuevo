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
  /** Mail del cliente, para «Consultar al cliente» desde la ventana de errores. */
  email?: string;
}

export interface ClienteEntrada {
  nombre: string;
  arca: { cuit_login: string; clave: string; cuit_representado: string };
  email?: string;
}

export interface Ajustes {
  carpeta_salida: string;
  pausa_segundos: number;
  /** true: el backend corre en esta PC (app de escritorio). false: corre en un servidor. Las descargas van siempre por el navegador. */
  dialogos_locales: boolean;
  /** false: los bots usan Google Chrome y la PC donde corre el backend no lo tiene. */
  chrome_instalado: boolean;
  /** Número de versión (archivo VERSION) y, en el instalador, fecha y hora del armado. */
  version?: string;
  compilado?: string | null;
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
  /** Los clientes del control, en orden (para reconocerlo en el historial). */
  nombres?: string[];
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

/** «Convertir AUX»: por cliente y período, el .zip (base64) y las partidas de ARCA no encontradas por operación. */
export interface ResultadoConversion {
  cliente: string;
  periodo: string;
  archivos: string[];
  partidas: Partial<Record<'Ventas' | 'Compras', number>>;
  /** Los comprobantes de cada .zip, para mostrarlos antes de subir. */
  filas?: Partial<Record<'Ventas' | 'Compras', { fecha: string; tipo: string; comprobante: string; cuit: string; razon: string; total: string | number }[]>>;
  /** Un .zip por operación con partidas, cada uno con un solo CSV (lo que pide Xubio). */
  zips: Partial<Record<'Ventas' | 'Compras', { nombre: string; contenido: string }>>;
  /** Copia para mirar en Excel: CAE y CUIT como texto. */
  excel: { nombre: string; contenido: string } | null;
  /** Lo que no generó archivo, por ejemplo «Ventas: no hay partidas…». */
  avisos: string[];
  error: string | null;
}

/** Emisor o receptor de un comprobante: tipo de documento de ARCA (80 CUIT, 96 DNI, 99 consumidor final). */
export interface ParteComprobante {
  doc_tipo: number | null;
  doc_nro: string;
  nombre: string;
}

/** Un comprobante leído de un PDF, foto, CSV o planilla, para revisar antes de subirlo a Xubio. */
export interface ComprobanteExtraido {
  id: string;
  archivo: string;
  pagina: number | null;
  /** Cómo se leyó: el QR de ARCA es exacto; el OCR conviene revisarlo. */
  metodo: 'planilla' | 'qr' | 'pdf' | 'ocr' | 'manual';
  incluir: boolean;
  operacion: 'Ventas' | 'Compras' | null;
  /** El cliente del estudio (la empresa en Xubio). */
  cliente: string;
  /** AAAA-MM-DD. */
  fecha: string;
  tipo: number | null;
  punto_venta: number | null;
  numero: number | null;
  cae: string;
  emisor: ParteComprobante;
  receptor: ParteComprobante;
  moneda: string;
  tipo_cambio: number;
  /** Con las columnas del CSV de Mis Comprobantes («IVA 21%», «Imp. Total»…). */
  importes: Record<string, number | null>;
  avisos: string[];
}

/** Qué pasó con cada archivo al leerlo: los AUX van por «Convertir AUX». */
export interface ArchivoLeido {
  nombre: string;
  tipo: 'aux' | 'comprobantes' | 'error';
  cantidad: number;
  avisos: string[];
}

/** Un AUX subido: nombre y contenido en base64. */
export interface ArchivoSubido {
  nombre: string;
  contenido: string;
  /** «CUIT|comprobante» que no van al .zip (destildadas al revisar el control). */
  excluir?: string[];
}

export interface ClienteEjecucion {
  nombre: string;
  estado: EstadoCliente;
  carpeta: string;
  archivos: ArchivoGenerado[];
  pasos: PasoEjecucion[];
  /** Link de Xubio ya parado en la empresa del cliente (lo guarda el bot). */
  xubio_url?: string | null;
  /** «Aplicar cambios»: por operación, el Libro IVA corregido y el control re-chequeado. */
  corregidos?: Partial<Record<'Ventas' | 'Compras', Corregido>>;
}

/** Lo que dejó «Aplicar cambios» en una operación (corrige el mismo Libro IVA y AUX del control). */
export interface Corregido {
  libro: string;
  aux: string;
  fecha: string;
  cambios: { campo: CampoCambio; cuit: string; pv: number; numero: number; comprobante: string; valor_arca: string | number; antes?: unknown; despues?: unknown }[];
}

export type CampoCambio = 'letra' | 'tipo' | 'importe' | 'cuit';

/** Un dato de ARCA para escribir en el Libro IVA (lo que manda «Aplicar cambios»). */
export interface CambioElegido {
  cliente: string;
  operacion: 'Ventas' | 'Compras';
  campo: CampoCambio;
  /** CUIT y comprobante como están en el Libro IVA (la fila a corregir). */
  cuit: string;
  pv: number;
  numero: number;
  comprobante: string;
  valorArca: string | number;
}

export interface ResultadoAplicar {
  resultados: {
    cliente: string;
    indice: number;
    operacion: string;
    aplicados: number;
    no_aplicados: (CambioElegido & { motivo: string })[];
    libro: string | null;
    aux: string | null;
    /** Esa operación no se pudo aplicar (las demás sí). */
    error?: string;
  }[];
  ejecucion: DetalleEjecucion;
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

/** Una fila de la hoja «Faltantes en Libro IVA …» del AUX, tal como está en el Excel. */
export interface FilaFaltanteExcel {
  fecha: string;
  /** «1 - Factura A». */
  tipo: string;
  /** «A-00014-01481153». */
  comprobante: string;
  /** Solo dígitos. */
  cuit: string;
  razon: string;
  total: number | null;
}

/** Lo que no cruzó de un cliente, tal como está en sus AUX, por operación (solo las que tienen AUX). */
export interface FaltantesExcel {
  /** Partidas de ARCA no encontradas en el Libro IVA (hoja «Faltantes en Libro IVA …»). */
  sin_libro: Partial<Record<'Ventas' | 'Compras', FilaFaltanteExcel[]>>;
  /** Partidas del Libro IVA no encontradas en ARCA (bloque al pie de la primera hoja). */
  sin_arca: Partial<Record<'Ventas' | 'Compras', FilaFaltanteExcel[]>>;
}

/** Una fila de ARCA o de Xubio que forma parte de un error. */
export interface FilaConError {
  origen: 'arca' | 'xubio';
  fila: CeldaVista[];
  /** Solo ARCA: las columnas de «Mis Comprobantes» para la tabla del mail al cliente. */
  mail?: CeldaVista[];
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
  | 'cuit_distinto'
  | 'importe_distinto'
  | 'total_no_cierra'
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
  /** Cuántos hay que tocar de verdad (sin los normales). No viene en controles viejos. */
  a_corregir?: number;
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

// --- Conversor De Facturas (backend/app/extraccion/a_excel.py) ---------------------------

export type NivelVerificacion = 'ok' | 'aviso' | 'error';

export interface ItemLeido {
  codigo: string;
  descripcion: string;
  cantidad: number | null;
  precio: number | null;
  alicuota: number | null;
  importe: number | null;
  /** Impuestos internos del ítem (bebidas, cigarrillos, combustibles…). */
  impuestos_internos?: number | null;
}

export interface HojaLeida {
  nombre: string;
  filas: (string | number | null)[][];
}

export interface Verificacion {
  /** emisor_cuit, neto_gravado, total, items, hojas… */
  campo: string;
  nivel: NivelVerificacion;
  mensaje: string;
  /** El valor que dan las cuentas (se ofrece «Usar»). */
  sugerencia: string | number | null;
  /** Corto, para la tarjeta; el mensaje queda en «¿Por qué?». */
  titulo?: string;
  datos?: string[];
  /** Lo que dio cada lectura, para elegir con un clic. */
  opciones?: string[];
}

/** Lo leído de un archivo: un comprobante (encabezado, ítems, totales) o una tabla (hojas). */
export interface LecturaExcel {
  archivo: string;
  tipo: 'comprobante' | 'tabla';
  /** «ia»: imagen leída con Gemini. */
  metodo: 'planilla' | 'pdf' | 'ocr' | 'ia';
  encabezado: Record<string, string>;
  items: ItemLeido[];
  totales: Record<string, number | null>;
  hojas: HojaLeida[];
  texto: string;
  /** Lo que cada pasada de OCR leyó distinto. */
  dudas: Record<string, string[]>;
  avisos: string[];
  verificaciones: Verificacion[];
  /** Solo si el archivo no se pudo leer. */
  error?: string;
}
