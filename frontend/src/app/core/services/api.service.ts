import { HttpClient, HttpErrorResponse } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable, TimeoutError, catchError, firstValueFrom, throwError, timeout } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AccionPausa, Ajustes, CambioElegido, ResultadoAplicar, Cliente, ErrorApi, ClienteEntrada, ControlesPorCliente, DetalleEjecucion, FaltantesExcel, PedidoEjecucion, ResumenEjecucion, ResultadoConversion, ArchivoSubido, ArchivoLeido, ComprobanteExtraido, LecturaExcel, Verificacion } from '../models';
import type { BloqueRegistro } from '../../features/ejecucion/ventana-errores';

/** Los pedidos a la API, sin estado. Las pantallas usan los stores. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly http = inject(HttpClient);
  private readonly url = environment.apiUrl;

  // Clientes
  clientes(): Promise<Cliente[]> {
    return firstValueFrom(this.http.get<Cliente[]>(`${this.url}/clientes`));
  }
  crearCliente(datos: ClienteEntrada): Promise<Cliente> {
    return firstValueFrom(this.http.post<Cliente>(`${this.url}/clientes`, datos));
  }
  editarCliente(nombreOriginal: string, datos: ClienteEntrada): Promise<Cliente> {
    return firstValueFrom(this.http.put<Cliente>(`${this.url}/clientes/${encodeURIComponent(nombreOriginal)}`, datos));
  }
  /** Agrega los clientes del estudio que falten (pide la contraseña de Clientes). */
  precargarClientes(): Promise<ResultadoPrecarga> {
    return firstValueFrom(this.http.post<ResultadoPrecarga>(`${this.url}/clientes/precargar`, {}));
  }
  borrarCliente(nombre: string): Promise<void> {
    return firstValueFrom(this.http.delete<void>(`${this.url}/clientes/${encodeURIComponent(nombre)}`));
  }

  // Controles: 1er / 2do control de cada cliente en el período (desde en DD/MM/AAAA).
  controles(clientes: string[], desde: string): Promise<ControlesPorCliente> {
    return firstValueFrom(this.http.post<ControlesPorCliente>(`${this.url}/controles`, { clientes, desde }));
  }

  // Convertir AUX: la hoja «Faltantes en Libro IVA» al CSV de Mis Comprobantes, sin tocar los controles.
  convertirAux(archivos: ArchivoSubido[]): Promise<{ resultados: ResultadoConversion[] }> {
    return firstValueFrom(
      this.http.post<{ resultados: ResultadoConversion[] }>(`${this.url}/convertir-aux`, { archivos }).pipe(errorLegible('No se pudieron convertir los AUX.')),
    );
  }

  /** «Conversor De Facturas»: lo leído de cada archivo, con la verificación. */
  leerAExcel(archivos: ArchivoSubido[]): Promise<{ lecturas: LecturaExcel[] }> {
    return firstValueFrom(
      this.http.post<{ lecturas: LecturaExcel[] }>(`${this.url}/a-excel/leer`, { archivos }).pipe(errorLegible('No se pudieron leer los archivos.')),
    );
  }
  /** La verificación de nuevo, con lo corregido en pantalla. */
  verificarAExcel(lectura: LecturaExcel): Promise<{ verificaciones: Verificacion[] }> {
    return firstValueFrom(
      this.http.post<{ verificaciones: Verificacion[] }>(`${this.url}/a-excel/verificar`, { lectura }).pipe(errorLegible('No se pudo verificar.')),
    );
  }
  /** El .xlsx (en base64). */
  generarAExcel(lectura: LecturaExcel, extra: { operacion?: string; empresa?: string; extras?: unknown } = {}): Promise<{ nombre: string; contenido: string }> {
    return firstValueFrom(
      this.http.post<{ nombre: string; contenido: string }>(`${this.url}/a-excel/generar`, { lectura, ...extra }).pipe(errorLegible('No se pudo armar el Excel.')),
    );
  }
  /** Lee los comprobantes de PDF, fotos, CSV y planillas (los AUX vuelven marcados para convertirAux). */
  extraerComprobantes(archivos: ArchivoSubido[]): Promise<{ archivos: ArchivoLeido[]; comprobantes: ComprobanteExtraido[] }> {
    return firstValueFrom(
      this.http
        .post<{ archivos: ArchivoLeido[]; comprobantes: ComprobanteExtraido[] }>(`${this.url}/comprobantes/extraer`, { archivos })
        .pipe(errorLegible('No se pudieron leer los archivos.')),
    );
  }

  /** Los comprobantes revisados, armados como «Convertir AUX» (un .zip por operación y cliente). */
  prepararComprobantes(comprobantes: ComprobanteExtraido[]): Promise<{ resultados: ResultadoConversion[] }> {
    return firstValueFrom(
      this.http
        .post<{ resultados: ResultadoConversion[] }>(`${this.url}/comprobantes/preparar`, { comprobantes })
        .pipe(errorLegible('No se pudieron preparar los comprobantes.')),
    );
  }

  /** Abre Xubio, carga el .zip en «Importar desde Mis Comprobantes (ARCA)», aprieta «Importar» y devuelve lo que dijo Xubio. */
  cargarEnXubio(cliente: string, operacion: 'Ventas' | 'Compras', zip: string): Promise<ResultadoCargaXubio> {
    return firstValueFrom(
      this.http.post<ResultadoCargaXubio>(`${this.url}/convertir-aux/xubio`, { cliente, operacion, zip }).pipe(errorLegible('No se pudo subir a Xubio.')),
    );
  }

  // Ajustes
  ajustes(): Promise<Ajustes> {
    return firstValueFrom(this.http.get<Ajustes>(`${this.url}/ajustes`));
  }
  /** El backend (en esta PC) aprieta Ctrl+V en Gmail cuando se abre. */
  pegarMail(): Promise<void> {
    return firstValueFrom(this.http.post<void>(`${this.url}/mail/pegar`, {}));
  }
  olvidarXubio(): Promise<void> {
    return firstValueFrom(this.http.delete<void>(`${this.url}/ajustes/xubio`));
  }

  // Ejecuciones
  ejecuciones(): Promise<ResumenEjecucion[]> {
    return firstValueFrom(this.http.get<ResumenEjecucion[]>(`${this.url}/ejecuciones`));
  }
  /** Solo del historial: los archivos del control quedan en sus carpetas. */
  eliminarEjecucion(id: string): Promise<void> {
    return firstValueFrom(this.http.delete<void>(`${this.url}/ejecuciones/${id}`).pipe(errorLegible('No se pudo borrar el control del historial.')));
  }
  activa(): Promise<ResumenEjecucion | null> {
    return firstValueFrom(this.http.get<ResumenEjecucion | null>(`${this.url}/ejecuciones/activa`));
  }
  /** Arranca un control. Los errores llegan como `ErrorApi`, listos para mostrar. */
  iniciar(pedido: PedidoEjecucion): Promise<DetalleEjecucion> {
    return firstValueFrom(
      this.http.post<DetalleEjecucion>(`${this.url}/ejecuciones`, pedido).pipe(timeout(ESPERA_INICIAR_MS), errorLegible('No se pudo iniciar el control.')),
    );
  }
  ejecucion(id: string, logDesde = 0): Promise<DetalleEjecucion> {
    return firstValueFrom(this.http.get<DetalleEjecucion>(`${this.url}/ejecuciones/${id}`, { params: { log_desde: logDesde } }));
  }
  responder(id: string, accion: AccionPausa): Promise<ResumenEjecucion> {
    return firstValueFrom(this.http.post<ResumenEjecucion>(`${this.url}/ejecuciones/${id}/respuesta`, { accion }));
  }
  cancelar(id: string): Promise<ResumenEjecucion> {
    return firstValueFrom(this.http.post<ResumenEjecucion>(`${this.url}/ejecuciones/${id}/cancelar`, {}));
  }
  /** Escribe los datos de ARCA elegidos en el Libro IVA, vuelve a cruzar y devuelve el control nuevo. */
  aplicarCambios(id: string, cambios: CambioElegido[]): Promise<ResultadoAplicar> {
    return firstValueFrom(
      this.http.post<ResultadoAplicar>(`${this.url}/ejecuciones/${id}/aplicar-cambios`, { cambios }).pipe(errorLegible('No se pudieron aplicar los cambios.')),
    );
  }

  /** Las partidas de ARCA no encontradas en el Libro IVA, leídas de la hoja «Faltantes» de cada AUX. */
  faltantes(id: string, indice: number): Promise<FaltantesExcel> {
    return firstValueFrom(this.http.get<FaltantesExcel>(`${this.url}/ejecuciones/${id}/clientes/${indice}/faltantes`));
  }

  /** Los hallazgos del control en un .xlsx, como registro; con cliente, solo los suyos. */
  hallazgosExcel(id: string, cliente: string | null, bloques: BloqueRegistro[]): Promise<{ nombre: string; contenido: string }> {
    return firstValueFrom(this.http.post<{ nombre: string; contenido: string }>(`${this.url}/ejecuciones/${id}/hallazgos-excel`, { cliente, bloques }));
  }

  /** Las partidas de ARCA no encontradas en un .xlsx (formato «Mis Comprobantes»), para adjuntar al mail. */
  faltantesExcel(id: string, indice: number, operacion: string | null): Promise<{ nombre: string; contenido: string }> {
    const params: Record<string, string> = operacion ? { operacion } : {};
    return firstValueFrom(this.http.get<{ nombre: string; contenido: string }>(`${this.url}/ejecuciones/${id}/clientes/${indice}/faltantes-excel`, { params }));
  }

  /** Un archivo del control como Blob, para bajarlo por el navegador (la descarga lleva los encabezados de sesión). */
  archivo(id: string, indice: number, nombre: string): Promise<Blob> {
    return firstValueFrom(this.http.get(`${this.url}/ejecuciones/${id}/clientes/${indice}/archivos/${encodeURIComponent(nombre)}`, { responseType: 'blob' }));
  }
  /**
   * Con el backend en esta PC: copia los archivos del cliente a
   * Descargas/Cliente/mmaaaa/Control de facturas/N° control (sin .zip ni ventanas).
   */
  exportar(id: string, indice: number): Promise<ResultadoExportar> {
    return firstValueFrom(
      this.http
        .post<ResultadoExportar>(`${this.url}/ejecuciones/${id}/clientes/${indice}/exportar`, {}, { params: { ventana: false } })
        .pipe(errorLegible('No se pudieron guardar los archivos.')),
    );
  }
  /** Todos los archivos del cliente en un .zip con Cliente/mmaaaa/Control de facturas/N° control. */
  zip(id: string, indice: number): Promise<Blob> {
    return firstValueFrom(this.http.get(`${this.url}/ejecuciones/${id}/clientes/${indice}/archivos.zip`, { responseType: 'blob' }));
  }
}

export interface ResultadoPrecarga {
  agregados: string[];
  ya_estaban: string[];
  /** true si se reutilizó la clave fiscal de un cliente con el mismo CUIT de ingreso. */
  con_clave: boolean;
  /** Precargados que ya estaban sin clave y se completaron con la compartida. */
  completados: string[];
  /** El CUIT con el que entran a ARCA todos los precargados. */
  cuit_ingreso: string;
  clientes: Cliente[];
}

/** Iniciar valida credenciales y crea carpetas: más de 30 s es que el servidor no responde. */
const ESPERA_INICIAR_MS = 30_000;

/**
 * Operador de RxJS que traduce cualquier falla del pedido a un `ErrorApi` con un
 * mensaje que entiende el contador (sin códigos), y guarda el detalle técnico.
 */
export function errorLegible<T>(porDefecto: string) {
  return (fuente: Observable<T>): Observable<T> =>
    fuente.pipe(catchError((e: unknown) => throwError(() => traducirError(e, porDefecto))));
}

export function traducirError(e: unknown, porDefecto: string): ErrorApi {
  if (e instanceof ErrorApi) return e;
  if (e instanceof TimeoutError) {
    return new ErrorApi('El servidor tardó demasiado en responder. Revisá que el backend esté funcionando y probá de nuevo.', 'Timeout esperando la respuesta del servidor.', 0);
  }
  if (!(e instanceof HttpErrorResponse)) {
    return new ErrorApi(porDefecto, e instanceof Error ? e.message : String(e), 0);
  }
  const detalle = e.error?.detail;
  const delBackend = typeof detalle === 'string' ? detalle : typeof detalle?.mensaje === 'string' ? detalle.mensaje : '';
  const cuerpo = typeof e.error === 'string' ? e.error : JSON.stringify(e.error ?? {}, null, 2);
  const tecnico = `HTTP ${e.status} ${e.statusText || ''} · ${e.url ?? ''}
${cuerpo}`.trim();
  let mensaje: string;
  if (e.status === 0) mensaje = 'No se pudo hablar con el servidor. Revisá que el backend esté corriendo.';
  else if (e.status === 401 || e.status === 403) mensaje = delBackend || 'No tenés permiso para hacer esto.';
  else if (e.status === 404) mensaje = delBackend || 'No se encontró lo que se pidió en el servidor.';
  // Un 500 con mensaje propio del backend («No se pudo volver a cruzar…») dice
  // qué pasó: se muestra. Sin mensaje es un error no previsto.
  else if (e.status >= 500) mensaje = delBackend || 'El servidor tuvo un error interno. Probá de nuevo; si sigue, pasá el detalle técnico.';
  else mensaje = delBackend || porDefecto;
  return new ErrorApi(mensaje, tecnico, e.status);
}

export interface ResultadoExportar {
  /** Dónde quedaron (…\Cliente\mmaaaa\Control de facturas\N° control). */
  carpeta: string;
  elegida: boolean;
  copiados: string[];
  /** Los que no se pudieron copiar (por ejemplo, abiertos en Excel). */
  fallas: { nombre: string; motivo: string }[];
}

export interface ResultadoCargaXubio {
  cliente: string;
  operacion: string;
  archivo: string;
  archivo_cargado: boolean;
  boton_importar_habilitado: boolean;
  /** true: Xubio dijo que importó · false: no importó · null: no se reconoció la respuesta (mirar en Xubio). */
  importado: boolean | null;
  /** Lo que mostró Xubio después de «Importar». */
  mensaje: string;
  log: string[];
}
