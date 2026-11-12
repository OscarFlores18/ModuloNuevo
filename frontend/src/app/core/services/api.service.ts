import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { environment } from '../../../environments/environment';
import { Ajustes, Cliente, ClienteEntrada, DetalleEjecucion, PedidoEjecucion, ResumenEjecucion } from '../models';

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
  borrarCliente(nombre: string): Promise<void> {
    return firstValueFrom(this.http.delete<void>(`${this.url}/clientes/${encodeURIComponent(nombre)}`));
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
  responder(id: string, accion: 'continuar' | 'cancelar' | 'no_preguntar'): Promise<ResumenEjecucion> {
    return firstValueFrom(this.http.post<ResumenEjecucion>(`${this.url}/ejecuciones/${id}/respuesta`, { accion }));
  }
  cancelar(id: string): Promise<ResumenEjecucion> {
    return firstValueFrom(this.http.post<ResumenEjecucion>(`${this.url}/ejecuciones/${id}/cancelar`, {}));
  }
  /** El archivo como Blob (la descarga lleva el encabezado de sesión, no sirve un <a href>). */
  archivo(id: string, indice: number, nombre: string): Promise<Blob> {
    return firstValueFrom(
      this.http.get(`${this.url}/ejecuciones/${id}/clientes/${indice}/archivos/${encodeURIComponent(nombre)}`, { responseType: 'blob' }),
    );
  }
}
