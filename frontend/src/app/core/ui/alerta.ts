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
 * abajo, el mensaje o la lista. Con `descartable`, una ✕ lo cierra.
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
      </div>
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
  readonly cerrado = output<void>();

  protected readonly clase = computed(() => CLASES[this.nivel()]);
  /** alert interrumpe al lector de pantalla: solo para errores. */
  protected readonly rol = computed(() => (this.nivel() === 'error' ? 'alert' : 'status'));
}
