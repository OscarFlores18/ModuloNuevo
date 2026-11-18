import { Component, ElementRef, inject, input, signal } from '@angular/core';

let siguienteId = 0;

/**
 * Un «?» chiquito al lado de lo que puede confundir. Al pasar el mouse, al
 * enfocarlo con el teclado o al tocarlo (celular) muestra una o dos líneas de
 * explicación. Esc o tocar afuera lo cierra.
 */
@Component({
  selector: 'app-ayuda',
  host: {
    class: 'cf-ayuda',
    '(mouseenter)': 'abierto.set(true)',
    '(mouseleave)': 'abierto.set(false)',
    '(document:click)': 'alClicAfuera($event)',
    '(document:keydown.escape)': 'abierto.set(false)',
  },
  template: `
    <button
      type="button"
      class="cf-ayuda-boton"
      [attr.aria-label]="'Ayuda: ' + texto()"
      [attr.aria-describedby]="abierto() ? id : null"
      [attr.aria-expanded]="abierto()"
      (click)="$event.preventDefault(); $event.stopPropagation(); abierto.set(true)"
      (focus)="abierto.set(true)"
      (blur)="abierto.set(false)"
    >?</button>
    @if (abierto()) {
      <span class="cf-ayuda-globo" [class.cf-ayuda-izq]="lado() === 'izquierda'" role="tooltip" [id]="id">{{ texto() }}</span>
    }
  `,
})
export class Ayuda {
  private readonly el = inject(ElementRef<HTMLElement>);
  readonly texto = input.required<string>();
  /** Para los «?» pegados al borde derecho: el globo se abre hacia la izquierda. */
  readonly lado = input<'derecha' | 'izquierda'>('derecha');
  protected readonly abierto = signal(false);
  protected readonly id = `cf-ayuda-${++siguienteId}`;

  protected alClicAfuera(ev: Event): void {
    if (this.abierto() && !this.el.nativeElement.contains(ev.target as Node)) this.abierto.set(false);
  }
}
