import { Component, inject, output } from '@angular/core';
import { Perspectiva, PerspectivaService } from '../../core/services/perspectiva.service';

/** «¿Cómo deseas ingresar?»: sin usuario ni contraseña, dos tarjetas. */
@Component({
  selector: 'app-elegir-perfil',
  host: { class: 'pf-pantalla' },
  template: `
    <main class="pf-caja" aria-labelledby="t-perfil">
      <p class="pf-marca">Control de Facturas</p>
      <h1 id="t-perfil" class="pf-titulo">¿Cómo deseas ingresar?</h1>
      <div class="pf-opciones">
        @for (o of opciones; track o.id) {
          <button type="button" class="pf-opcion" (click)="elegir(o.id)">
            <span class="pf-icono" aria-hidden="true">
              @if (o.id === 'empleado') {
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></svg>
              } @else {
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z" /></svg>
              }
            </span>
            <span class="pf-nombre">{{ o.nombre }}</span>
            <span class="pf-detalle">{{ o.detalle }}</span>
          </button>
        }
      </div>
      <p class="mf-ayuda pf-nota">La elección dura mientras esté abierta esta pestaña. Se cambia con «Cambiar perfil», arriba a la derecha.</p>
    </main>
  `,
})
export class ElegirPerfil {
  private readonly perspectiva = inject(PerspectivaService);
  readonly elegido = output<Perspectiva>();

  protected readonly opciones: { id: Perspectiva; nombre: string; detalle: string }[] = [
    { id: 'empleado', nombre: 'Empleado', detalle: 'Consulta de ejecuciones, faltantes a Xubio y clientes. Puede descargar; el resto queda bloqueado.' },
    { id: 'total', nombre: 'Acceso Total', detalle: 'Nuevos controles, carga de faltantes a Xubio y edición de clientes.' },
  ];

  protected elegir(p: Perspectiva): void {
    this.perspectiva.elegir(p);
    this.elegido.emit(p);
  }
}
