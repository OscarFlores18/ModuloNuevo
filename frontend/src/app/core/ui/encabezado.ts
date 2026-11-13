import { Component, input } from '@angular/core';

/**
 * El encabezado de cada pantalla: ícono, título y una línea que explica para qué sirve.
 * El ícono entra por <ng-content select="[icono]"> (un <svg icono>); las acciones, a la derecha.
 */
@Component({
  selector: 'app-encabezado',
  host: { class: 'mb-8 flex flex-wrap items-start gap-4' },
  template: `
    <div class="flex h-14 w-14 shrink-0 items-center justify-center rounded-xl bg-brand-50 text-brand-700" aria-hidden="true">
      <ng-content select="[icono]" />
    </div>
    <div class="min-w-0 flex-1">
      <h1 class="text-2xl font-semibold">{{ titulo() }}</h1>
      @if (bajada()) {
        <p class="mt-1 max-w-3xl text-lg text-muted">{{ bajada() }}</p>
      }
    </div>
    <div class="flex shrink-0 items-center gap-2 self-center"><ng-content /></div>
  `,
})
export class Encabezado {
  readonly titulo = input.required<string>();
  readonly bajada = input('');
}
