import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { Verificacion } from '../../core/models';

/** Lo que hay que mirar de la lectura, corto: un título, los números que importan y qué hacer. */
@Component({
  selector: 'app-verificaciones',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; margin: 0 0 14px; }
    ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
    li { display: grid; grid-template-columns: 26px minmax(0, 1fr) auto; gap: 4px 10px; align-items: center; padding: 8px 10px; border-radius: 10px; border: 1px solid transparent; }
    li.error { background: #fef2f2; border-color: #fecaca; }
    li.aviso { background: #fffbeb; border-color: #fde68a; }
    .ic { width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; color: #fff; font-weight: 800; font-size: .8rem; }
    .error .ic { background: #dc2626; } .aviso .ic { background: #f59e0b; }
    .tit { font-weight: 700; font-size: .86rem; color: #1e293b; }
    .chips { grid-column: 2 / 4; display: flex; flex-wrap: wrap; gap: 4px; }
    .chip { padding: 2px 8px; border-radius: 999px; background: #fff; border: 1px solid #e2e8f0; font-size: .78rem; font-variant-numeric: tabular-nums; color: #334155; }
    .chip.dif { border-color: #fca5a5; color: #b91c1c; font-weight: 700; }
    button.chip { cursor: pointer; border-color: #93c5fd; color: #1d4ed8; font-weight: 600; }
    button.chip:hover { background: #eff6ff; }
    .acc { display: flex; gap: 6px; align-items: center; }
    .usar { padding: 4px 10px; border-radius: 8px; border: 0; background: #2563eb; color: #fff; font-size: .78rem; font-weight: 700; cursor: pointer; }
    details { grid-column: 2 / 4; font-size: .78rem; color: #64748b; }
    summary { cursor: pointer; width: fit-content; }
    .bien { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
    .bien span { padding: 3px 9px; border-radius: 999px; background: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; font-size: .78rem; font-weight: 600; }
  `,
  template: `
    <ul aria-live="polite">
      @for (v of aMirar(); track $index) {
        <li [class]="v.nivel">
          <span class="ic" aria-hidden="true">{{ v.nivel === 'error' ? '✕' : '!' }}</span>
          <span class="tit">{{ v.titulo || v.mensaje }}</span>
          <span class="acc">
            @if (v.sugerencia !== null && v.sugerencia !== undefined) {
              <button type="button" class="usar" (click)="usar.emit(v)">Usar {{ formatear(v.sugerencia) }}</button>
            }
          </span>
          @if (v.datos?.length || v.opciones?.length) {
            <div class="chips">
              @for (d of v.datos ?? []; track $index) { <span class="chip" [class.dif]="d.startsWith('Dif')">{{ d }}</span> }
              @for (o of v.opciones ?? []; track $index) {
                <button type="button" class="chip" (click)="elegir.emit([v.campo, o])" [title]="'Usar «' + o + '»'">{{ o }}</button>
              }
            </div>
          }
          @if (v.titulo) {
            <details><summary>¿Por qué?</summary>{{ v.mensaje }}</details>
          }
        </li>
      }
    </ul>
    @if (bien().length) {
      <div class="bien" aria-label="Verificado bien">
        @for (v of bien(); track $index) { <span [title]="v.mensaje">✓ {{ v.titulo || v.mensaje }}</span> }
      </div>
    }
  `,
})
export class Verificaciones {
  readonly lista = input.required<Verificacion[]>();
  readonly usar = output<Verificacion>();
  /** [campo, valor] elegido entre las lecturas que dieron distinto. */
  readonly elegir = output<[string, string]>();

  protected readonly aMirar = computed(() => this.lista().filter((v) => v.nivel !== 'ok'));
  protected readonly bien = computed(() => this.lista().filter((v) => v.nivel === 'ok'));

  protected formatear(v: string | number): string {
    return typeof v === 'number' ? v.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : v;
  }
}
