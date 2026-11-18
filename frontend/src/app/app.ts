import { Component, DestroyRef, inject, signal } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Usuario } from './core/models';
import { ClientesStore } from './core/services/clientes.store';
import { EjecucionStore } from './core/services/ejecucion.store';
import { PerspectivaService } from './core/services/perspectiva.service';
import { SesionService } from './core/services/sesion.service';
import { Toasts } from './core/ui/toast';
import { ElegirPerfil } from './features/perfil/elegir-perfil';

@Component({
  host: { '(window:scroll)': 'medirScroll()', '(window:resize)': 'medirScroll()' },
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Toasts, ElegirPerfil],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  protected readonly sesion = inject(SesionService);
  protected readonly ejecucion = inject(EjecucionStore);
  protected readonly clientes = inject(ClientesStore);
  protected readonly perspectiva = inject(PerspectivaService);
  private readonly router = inject(Router);

  /** Cuándo se trajeron por última vez los clientes y las ejecuciones del servidor. */
  protected readonly ultimaActualizacion = signal<Date | null>(null);
  protected readonly actualizando = signal(false);

  private readonly formatoFecha = new Intl.DateTimeFormat('es-AR', {
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });

  /** El botón flotante «Volver arriba». */
  protected readonly hayScroll = signal(false);

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

  /** Aparece recién después de bajar más de una pantalla: no tapa nada mientras se trabaja arriba. */
  protected medirScroll(): void {
    const el = document.documentElement;
    this.hayScroll.set(el.scrollTop > el.clientHeight * 0.8);
  }

  protected saltar(): void {
    window.scrollTo({ top: 0, behavior: 'smooth' });
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

  /** Vuelve a «¿Cómo deseas ingresar?». */
  protected cambiarPerfil(): void {
    this.perspectiva.cambiar();
  }

  /** Recién elegido: a la pantalla de inicio de esa perspectiva (el empleado no ve Nuevo control). */
  protected alElegirPerfil(): void {
    void this.router.navigateByUrl('/');
  }

  protected formatearFecha(d: Date): string {
    return this.formatoFecha.format(d).replace(',', '');
  }

  protected rolesDe(u: Usuario): string {
    const roles = u.roles.map((r) => (r === 'supervisor' ? 'Supervisor' : 'Usuario')).join(' · ') || 'Sin rol';
    return u.nivel_nombre ? `${u.nivel_nombre} · ${roles}` : roles;
  }
}
