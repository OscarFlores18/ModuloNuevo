import { Component, computed, input, output } from '@angular/core';

type Tono = 'info' | 'ok' | 'aviso' | 'error';

const CLASES: Record<Tono, string> = {
  info: 'mf-aviso',
  ok: 'mf-aviso mf-aviso-ok',
  aviso: 'mf-aviso mf-aviso-alerta',
  error: 'mf-aviso mf-aviso-error',
};

/**
 * Aviso con borde de color a la izquierda (`mf-aviso`): título en negrita y,
 * abajo, el mensaje o la lista. Con `orden`, qué hacer (en negrita, con flecha);
 * con `accion`, un botón a la derecha que lo resuelve. Con `descartable`, una ✕ lo cierra.
 */
@Component({
  selector: 'app-alerta',
  host: { class: 'block' },
  template: `
    <div class="flex items-start gap-3" [class]="clase()" [attr.role]="rol()">
      <div class="min-w-0 flex-1">
        @if (titulo()) { <strong>{{ titulo() }}</strong> }
        @if (mensajes().length === 1) {
          @if (titulo()) { <p>{{ mensajes()[0] }}</p> } @else { <span>{{ mensajes()[0] }}</span> }
        } @else if (mensajes().length > 1) {
          <ul>
            @for (m of mensajes(); track m) { <li>{{ m }}</li> }
          </ul>
        }
        @if (orden()) { <p class="mf-aviso-orden">→ {{ orden() }}</p> }
      </div>
      @if (accion()) {
        <button type="button" class="mf-btn mf-btn-chico shrink-0 self-center" (click)="accionar.emit()">{{ accion() }}</button>
      }
      @if (descartable()) {
        <button type="button" (click)="cerrado.emit()" class="mf-icono-btn -my-1 -mr-2" aria-label="Cerrar aviso">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" class="h-3.5 w-3.5" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
        </button>
      }
    </div>
  `,
})
export class Alerta {
  readonly mensajes = input<string[]>([]);
  readonly titulo = input('');
  readonly nivel = input<Tono>('aviso');
  readonly descartable = input(false);
  /** Qué hacer, en imperativo: «Corregí la clave fiscal de ARCA y volvé a correrlo». */
  readonly orden = input('');
  /** Texto del botón que lo resuelve («Ir a Clientes»). */
  readonly accion = input('');
  readonly cerrado = output<void>();
  readonly accionar = output<void>();

  protected readonly clase = computed(() => CLASES[this.nivel()]);
  /** alert interrumpe al lector de pantalla: solo para errores. */
  protected readonly rol = computed(() => (this.nivel() === 'error' ? 'alert' : 'status'));
}
