import { Injectable, computed, inject, signal } from '@angular/core';
import { Cliente, ClienteEntrada, mensajeDeError } from '../models';
import { ApiService } from './api.service';

/** La lista de clientes (config.json), compartida por «Nuevo control» y «Clientes». */
@Injectable({ providedIn: 'root' })
export class ClientesStore {
  private readonly api = inject(ApiService);

  readonly lista = signal<Cliente[]>([]);
  readonly cargando = signal(false);
  readonly cargado = signal(false);
  readonly error = signal<string | null>(null);

  readonly incompletos = computed(() => this.lista().filter((c) => c.faltantes.length).length);

  async cargar(forzar = false): Promise<void> {
    if (this.cargando() || (this.cargado() && !forzar)) return;
    this.cargando.set(true);
    try {
      this.lista.set(await this.api.clientes());
      this.error.set(null);
      this.cargado.set(true);
    } catch (e) {
      this.error.set(mensajeDeError(e, 'No se pudo cargar la lista de clientes.'));
    } finally {
      this.cargando.set(false);
    }
  }

  /** Alta (sin nombre original) o edición. Tira el error con el mensaje de la API. */
  async guardar(nombreOriginal: string | null, datos: ClienteEntrada): Promise<Cliente> {
    const cliente = nombreOriginal ? await this.api.editarCliente(nombreOriginal, datos) : await this.api.crearCliente(datos);
    const resto = this.lista().filter((c) => c.nombre !== nombreOriginal && c.nombre !== cliente.nombre);
    this.lista.set([...resto, cliente].sort((a, b) => a.nombre.localeCompare(b.nombre, 'es', { sensitivity: 'base' })));
    return cliente;
  }

  async borrar(nombre: string): Promise<void> {
    await this.api.borrarCliente(nombre);
    this.lista.update((l) => l.filter((c) => c.nombre !== nombre));
  }
}
