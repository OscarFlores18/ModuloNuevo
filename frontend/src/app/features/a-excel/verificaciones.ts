import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { Verificacion } from '../../core/models';

/** Lo que hay que mirar de la lectura, corto: un título, los números que importan y qué hacer. */
@Component({
  selector: 'app-verificaciones',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; margin: 0 0 10px; }
    ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; }
    li { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; padding: 5px 8px; border-radius: 8px; border-left: 3px solid; font-size: .8rem; }
    li.error { background: #fef2f2; border-color: #dc2626; }
    li.aviso { background: #fffbeb; border-color: #f59e0b; }
    .ic { font-weight: 800; } .error .ic { color: #dc2626; } .aviso .ic { color: #d97706; }
    .tit { font-weight: 700; color: #1e293b; }
    .chip { padding: 1px 7px; border-radius: 999px; background: #fff; border: 1px solid #e2e8f0; font-size: .74rem; font-variant-numeric: tabular-nums; color: #334155; }
    .chip.dif { border-color: #fca5a5; color: #b91c1c; font-weight: 700; }
    button.chip { cursor: pointer; border-color: #93c5fd; color: #1d4ed8; font-weight: 600; }
    button.chip:hover { background: #eff6ff; }
    .por { cursor: help; color: #94a3b8; font-size: .8rem; }
    .usar { margin-left: auto; padding: 2px 9px; border-radius: 6px; border: 0; background: #2563eb; color: #fff; font-size: .74rem; font-weight: 700; cursor: pointer; }
    .bien { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
    .bien span { padding: 1px 8px; border-radius: 999px; background: #f0fdf4; border: 1px solid #bbf7d0; color: #166534; font-size: .72rem; font-weight: 600; }
  `,
  template: `
    <ul aria-live="polite">
      @for (v of aMirar(); track $index) {
        <li [class]="v.nivel">
          <span class="ic" aria-hidden="true">{{ v.nivel === 'error' ? '✕' : '!' }}</span>
          <span class="tit">{{ v.titulo || v.mensaje }}</span>
          @for (d of v.datos ?? []; track $index) { <span class="chip" [class.dif]="d.startsWith('Dif')">{{ d }}</span> }
          @for (o of v.opciones ?? []; track $index) {
            <button type="button" class="chip" (click)="elegir.emit([v.campo, o])" [title]="'Usar «' + o + '»'">{{ o }}</button>
          }
          @if (v.titulo) { <span class="por" [title]="v.mensaje" role="note" [attr.aria-label]="v.mensaje">ⓘ</span> }
          @if (v.sugerencia !== null && v.sugerencia !== undefined) {
            <button type="button" class="usar" (click)="usar.emit(v)">Usar {{ formatear(v.sugerencia) }}</button>
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
