import { Component, Injectable, inject, signal, untracked } from '@angular/core';

export type TipoToast = 'ok' | 'info' | 'aviso' | 'error';

export interface Toast {
  id: number;
  tipo: TipoToast;
  titulo: string;
  detalle?: string;
}

/** Cuánto queda en pantalla cada tipo (ms). Los errores, más: hay que leerlos. */
const DURACION: Record<TipoToast, number> = { ok: 4000, info: 4000, aviso: 6000, error: 8000 };
const MAXIMO = 4;

/**
 * Notificaciones no bloqueantes. Cualquier pantalla las dispara con
 * `inject(ToastService).error('No se pudo…', detalle)`; el contenedor vive en el
 * shell (app.html). Un mismo mensaje repetido no se apila: se renueva.
 */
@Injectable({ providedIn: 'root' })
export class ToastService {
  readonly lista = signal<Toast[]>([]);
  private siguiente = 0;
  private readonly timers = new Map<number, ReturnType<typeof setTimeout>>();

  ok(titulo: string, detalle?: string): void {
    this.mostrar('ok', titulo, detalle);
  }
  info(titulo: string, detalle?: string): void {
    this.mostrar('info', titulo, detalle);
  }
  aviso(titulo: string, detalle?: string): void {
    this.mostrar('aviso', titulo, detalle);
  }
  error(titulo: string, detalle?: string): void {
    this.mostrar('error', titulo, detalle);
  }

  /**
   * Se puede llamar desde un effect(): la lista se lee y se escribe sin
   * registrarla como dependencia. Si no, el effect que muestra un toast
   * dependería de la lista que él mismo cambia y se volvería a correr sin fin
   * (la página quedaba trabada cuando el backend no respondía).
   */
  mostrar(tipo: TipoToast, titulo: string, detalle?: string): void {
    untracked(() => {
      const repetido = this.lista().find((t) => t.tipo === tipo && t.titulo === titulo && t.detalle === detalle);
      if (repetido) this.cerrar(repetido.id);
      const toast: Toast = { id: ++this.siguiente, tipo, titulo, detalle };
      this.lista.update((l) => [...l, toast].slice(-MAXIMO));
      this.timers.set(toast.id, setTimeout(() => this.cerrar(toast.id), DURACION[tipo]));
    });
  }

  cerrar(id: number): void {
    clearTimeout(this.timers.get(id));
    this.timers.delete(id);
    untracked(() => this.lista.update((l) => l.filter((t) => t.id !== id)));
  }
}

const CLASE: Record<TipoToast, string> = {
  ok: 'mf-aviso mf-aviso-ok',
  info: 'mf-aviso',
  aviso: 'mf-aviso mf-aviso-alerta',
  error: 'mf-aviso mf-aviso-error',
};

/** El contenedor de las notificaciones: abajo a la derecha, sin tapar el formulario. */
@Component({
  selector: 'app-toasts',
  host: { class: 'pointer-events-none fixed right-4 bottom-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2' },
  template: `
    @for (t of servicio.lista(); track t.id) {
      <div class="pointer-events-auto flex items-start gap-3 !bg-panel shadow-lg" [class]="clase[t.tipo]"
        [attr.role]="t.tipo === 'error' ? 'alert' : 'status'">
        <div class="min-w-0 flex-1">
          <strong class="block">{{ t.titulo }}</strong>
          @if (t.detalle) { <p class="mf-tenue !mt-0.5 text-sm break-words">{{ t.detalle }}</p> }
        </div>
        <button type="button" class="mf-icono-btn -my-1 -mr-2" (click)="servicio.cerrar(t.id)" aria-label="Cerrar notificación">✕</button>
      </div>
    }
  `,
})
export class Toasts {
  protected readonly servicio = inject(ToastService);
  protected readonly clase = CLASE;
}
