import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';
import { AccionPausa, Ajustes, Cliente, ClienteEntrada, ControlesPorCliente, DetalleEjecucion, PedidoEjecucion, ResumenEjecucion } from '../models';

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

  // Ajustes
  ajustes(): Promise<Ajustes> {
    return firstValueFrom(this.http.get<Ajustes>(`${this.url}/ajustes`));
  }
  olvidarXubio(): Promise<void> {
    return firstValueFrom(this.http.delete<void>(`${this.url}/ajustes/xubio`));
  }

  // Ejecuciones
  ejecuciones(): Promise<ResumenEjecucion[]> {
    return firstValueFrom(this.http.get<ResumenEjecucion[]>(`${this.url}/ejecuciones`));
  }
  activa(): Promise<ResumenEjecucion | null> {
    return firstValueFrom(this.http.get<ResumenEjecucion | null>(`${this.url}/ejecuciones/activa`));
  }
  iniciar(pedido: PedidoEjecucion): Promise<DetalleEjecucion> {
    return firstValueFrom(this.http.post<DetalleEjecucion>(`${this.url}/ejecuciones`, pedido));
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
  /** El servidor abre la ventana para elegir carpeta y copia ahí los archivos (ver ResultadoExportar). */
  exportar(id: string, indice: number): Promise<ResultadoExportar> {
    return firstValueFrom(this.http.post<ResultadoExportar>(`${this.url}/ejecuciones/${id}/clientes/${indice}/exportar`, {}));
  }
  /** Modo servidor: el archivo como Blob (la descarga lleva los encabezados de sesión). */
  archivo(id: string, indice: number, nombre: string): Promise<Blob> {
    return firstValueFrom(this.http.get(`${this.url}/ejecuciones/${id}/clientes/${indice}/archivos/${encodeURIComponent(nombre)}`, { responseType: 'blob' }));
  }
  /** Modo servidor: todos los archivos del cliente en un .zip con Cliente/mmaaaa/Control de facturas. */
  zip(id: string, indice: number): Promise<Blob> {
    return firstValueFrom(this.http.get(`${this.url}/ejecuciones/${id}/clientes/${indice}/archivos.zip`, { responseType: 'blob' }));
  }
  /** El servidor abre «Guardar como» y copia ahí ese archivo. */
  guardarArchivo(id: string, indice: number, nombre: string): Promise<{ cancelado: true } | { cancelado?: false; ruta: string }> {
    return firstValueFrom(
      this.http.post<{ cancelado: true } | { cancelado?: false; ruta: string }>(
        `${this.url}/ejecuciones/${id}/clientes/${indice}/archivos/${encodeURIComponent(nombre)}/guardar`,
        {},
      ),
    );
  }
}

export interface ResultadoPrecarga {
  agregados: string[];
  ya_estaban: string[];
  /** true si se reutilizó la clave fiscal de un cliente con el mismo CUIT de ingreso. */
  con_clave: boolean;
  /** Precargados que ya estaban sin clave y se completaron con la compartida. */
  completados: string[];
  clientes: Cliente[];
}

/** `elegida`: false si se cerró la ventana y se creó la carpeta del cliente en Descargas. */
export interface ResultadoExportar {
  carpeta: string;
  elegida: boolean;
  copiados: string[];
  fallas: { nombre: string; motivo: string }[];
}
