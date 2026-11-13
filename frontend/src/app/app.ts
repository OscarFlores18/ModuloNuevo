import { Component, DestroyRef, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Usuario } from './core/models';
import { ClientesStore } from './core/services/clientes.store';
import { EjecucionStore } from './core/services/ejecucion.store';
import { SesionService, USUARIOS_DE_PRUEBA } from './core/services/sesion.service';
import { Toasts } from './core/ui/toast';

@Component({
  host: { '(window:scroll)': 'medirScroll()', '(window:resize)': 'medirScroll()' },
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Toasts],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  protected readonly sesion = inject(SesionService);
  protected readonly ejecucion = inject(EjecucionStore);
  protected readonly clientes = inject(ClientesStore);
  protected readonly usuariosDePrueba = USUARIOS_DE_PRUEBA;

  /** Cuándo se trajeron por última vez los clientes y las ejecuciones del servidor. */
  protected readonly ultimaActualizacion = signal<Date | null>(null);
  protected readonly actualizando = signal(false);

  private readonly formatoFecha = new Intl.DateTimeFormat('es-AR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  });

  /** El botón flotante solo aparece si la página es más alta que la ventana. */
  protected readonly hayScroll = signal(false);
  /** true = ya se está al final: el botón lleva arriba. */
  protected readonly abajo = signal(false);

  constructor() {
    // Para el contador de clientes en la pestaña.
    void this.actualizar();
    // El alto de la página cambia al navegar o cuando crece el contenido (registro, tablas).
    if (typeof ResizeObserver !== 'undefined') {
      const observador = new ResizeObserver(() => this.medirScroll());
      observador.observe(document.body);
      inject(DestroyRef).onDestroy(() => observador.disconnect());
    }
  }

  protected medirScroll(): void {
    const el = document.documentElement;
    const sobra = el.scrollHeight - el.clientHeight;
    this.hayScroll.set(sobra > 200);
    this.abajo.set(sobra - el.scrollTop < 120);
  }

  protected saltar(): void {
    const el = document.documentElement;
    window.scrollTo({ top: this.abajo() ? 0 : el.scrollHeight, behavior: 'smooth' });
  }

  /** «Actualizar» del encabezado: vuelve a pedir clientes, la ejecución en curso y el historial. */
  protected async actualizar(): Promise<void> {
    if (this.actualizando()) return;
    this.actualizando.set(true);
    try {
      await Promise.all([this.clientes.cargar(true), this.ejecucion.revisarActiva(), this.ejecucion.cargarHistorial()]);
      if (!this.clientes.error()) this.ultimaActualizacion.set(new Date());
    } finally {
      this.actualizando.set(false);
    }
  }

  protected formatearFecha(d: Date): string {
    return this.formatoFecha.format(d);
  }

  protected rolesDe(u: Usuario): string {
    const roles = u.roles.map((r) => (r === 'supervisor' ? 'Supervisor' : 'Usuario')).join(' · ') || 'Sin rol';
    return u.nivel_nombre ? `${u.nivel_nombre} · ${roles}` : roles;
  }

  protected iniciales(nombre: string): string {
    const partes = nombre.match(/\p{L}+/gu) ?? [];
    return ((partes[0]?.[0] ?? '?') + (partes[1]?.[0] ?? '')).toUpperCase();
  }

  protected cambiarUsuario(id: string): void {
    this.sesion.cambiar(id);
  }
}
