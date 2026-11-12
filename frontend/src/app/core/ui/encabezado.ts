import { Component, input } from '@angular/core';

/**
 * El título de cada sección, debajo de las pestañas: nombre, una línea que
 * explica para qué sirve y, a la derecha, las acciones (<ng-content>).
 */
@Component({
  selector: 'app-encabezado',
  host: { class: 'flex flex-wrap items-end justify-between gap-4' },
  template: `
    <div class="min-w-0">
      <h2 class="m-0 text-xl font-semibold">{{ titulo() }}</h2>
      @if (bajada()) { <p class="mf-sub m-0 mt-0.5">{{ bajada() }}</p> }
    </div>
    <div class="flex shrink-0 items-center gap-2"><ng-content /></div>
  `,
})
export class Encabezado {
  readonly titulo = input.required<string>();
  readonly bajada = input('');
}
