import { Injectable, PLATFORM_ID, computed, inject, signal } from '@angular/core';
import { isPlatformBrowser } from '@angular/common';
import { DetalleEjecucion, LineaLog, PedidoEjecucion, ResumenEjecucion, estaActiva, mensajeDeError } from '../models';
import { ApiService } from './api.service';

const CADA_MS = 1200;

/**
 * La ejecución que se está mirando y el historial reciente.
 *
 * Mientras una ejecución está activa se consulta cada poco más de un segundo y
 * el registro llega por partes (`log_desde`): solo las líneas nuevas.
 */
@Injectable({ providedIn: 'root' })
export class EjecucionStore {
  private readonly api = inject(ApiService);
  private readonly navegador = isPlatformBrowser(inject(PLATFORM_ID));

  readonly actual = signal<DetalleEjecucion | null>(null);
  readonly log = signal<LineaLog[]>([]);
  readonly historial = signal<ResumenEjecucion[]>([]);
  readonly error = signal<string | null>(null);
  readonly enviando = signal(false);
  /** Hay una corriendo en el servidor (para el menú), aunque no sea la que se mira. */
  readonly hayActiva = signal(false);

  readonly activa = computed(() => estaActiva(this.actual()?.estado));

  private temporizador: ReturnType<typeof setTimeout> | null = null;
  private siguiendo: string | null = null;

  /** Al abrir la app: si hay una ejecución en curso, se la sigue (el menú muestra el punto). */
  async revisarActiva(): Promise<void> {
    if (!this.navegador) return;
    try {
      const a = await this.api.activa();
      this.hayActiva.set(!!a);
      if (a && !this.siguiendo) this.seguir(a.id);
    } catch {
      // Sin backend: cada pantalla avisa por su cuenta.
    }
  }

  async cargarHistorial(): Promise<void> {
    try {
      this.historial.set(await this.api.ejecuciones());
    } catch (e) {
      this.error.set(mensajeDeError(e));
    }
  }

  async iniciar(pedido: PedidoEjecucion): Promise<DetalleEjecucion> {
    this.enviando.set(true);
    try {
      const ej = await this.api.iniciar(pedido);
      this.mostrar(ej, true);
      this.hayActiva.set(true);
      this.programar();
      return ej;
    } finally {
      this.enviando.set(false);
    }
  }

  /** Empieza a mirar una ejecución (la activa o una del historial). */
  seguir(id: string): void {
    if (this.siguiendo === id && this.actual()) return;
    this.detener();
    this.siguiendo = id;
    this.actual.set(null);
    this.log.set([]);
    void this.consultar();
  }

  async responder(accion: 'continuar' | 'cancelar' | 'no_preguntar'): Promise<void> {
    const ej = this.actual();
    if (!ej) return;
    try {
      await this.api.responder(ej.id, accion);
      // La pausa se va enseguida: no hace falta esperar a la próxima consulta.
      this.actual.set({ ...ej, pausa: null, estado: 'en_curso', preguntar: accion === 'no_preguntar' ? false : ej.preguntar });
      void this.consultar();
    } catch (e) {
      this.error.set(mensajeDeError(e));
    }
  }

  async cancelar(): Promise<void> {
    const ej = this.actual();
    if (!ej) return;
    try {
      await this.api.cancelar(ej.id);
      this.actual.set({ ...ej, cancelar_pedido: true });
      void this.consultar();
    } catch (e) {
      this.error.set(mensajeDeError(e));
    }
  }

  private mostrar(ej: DetalleEjecucion, desdeCero: boolean): void {
    this.siguiendo = ej.id;
    this.actual.set(ej);
    this.log.update((l) => (desdeCero ? ej.log : [...l, ...ej.log]));
    this.error.set(null);
  }

  private async consultar(): Promise<void> {
    const id = this.siguiendo;
    if (!id) return;
    const previo = this.actual();
    const desde = previo?.id === id ? previo.log_total : 0;
    try {
      const ej = await this.api.ejecucion(id, desde);
      if (this.siguiendo !== id) return; // se cambió de ejecución mientras tanto
      this.mostrar(ej, desde === 0);
      const sigue = estaActiva(ej.estado);
      this.hayActiva.set(sigue);
      if (sigue) this.programar();
      else void this.cargarHistorial();
    } catch (e) {
      this.error.set(mensajeDeError(e));
      // Un corte de red no deja la pantalla congelada: se reintenta.
      if (estaActiva(previo?.estado)) this.programar(CADA_MS * 3);
    }
  }

  private programar(ms = CADA_MS): void {
    if (!this.navegador) return;
    if (this.temporizador) clearTimeout(this.temporizador);
    this.temporizador = setTimeout(() => {
      this.temporizador = null;
      void this.consultar();
    }, ms);
  }

  private detener(): void {
    if (this.temporizador) clearTimeout(this.temporizador);
    this.temporizador = null;
  }
}
