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

  private readonly cortas = computed(() => this.lista().map(corta));
  protected readonly aMirar = computed(() => this.cortas().filter((v) => v.nivel !== 'ok'));
  protected readonly bien = computed(() => this.cortas().filter((v) => v.nivel === 'ok'));

  protected formatear(v: string | number): string {
    return typeof v === 'number' ? v.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : v;
  }
}

/** Si el backend no mandó el título corto (versión anterior), se arma del mensaje. */
export function corta(v: Verificacion): Verificacion {
  if (v.titulo) return v;
  const m = v.mensaje;
  let x: RegExpMatchArray | null;
  if ((x = m.match(/^Los \d+ ítems suman ([\d.,-]+) y el neto gravado es ([\d.,-]+) \(diferencia ([\d.,-]+)\)/)))
    return { ...v, titulo: 'Los ítems no suman el neto', datos: [`Ítems ${x[1]}`, `Neto ${x[2]}`, `Dif. ${x[3]}`] };
  if ((x = m.match(/^Los ítems (\d+) y (\d+) tienen la misma cantidad y precio pero distinto importe \(([\d.,-]+) y ([\d.,-]+)\)/)))
    return { ...v, titulo: `Ítems ${x[1]} y ${x[2]}: mismo precio, otro importe`, datos: [`#${x[1]} ${x[3]}`, `#${x[2]} ${x[4]}`] };
  if ((x = m.match(/^(.+?): cada lectura dio algo distinto \((.+)\)\./)))
    return { ...v, titulo: `${x[1]}: ¿cuál es?`, opciones: x[2].split(' / ') };
  if ((x = m.match(/^(.+?): el dígito verificador da bien/))) return { ...v, titulo: x[1] };
  if (/^Neto \+ IVA .*= total/.test(m)) return { ...v, titulo: 'El total cierra' };
  if ((x = m.match(/^El IVA es el ([\d.,]+)% /))) return { ...v, titulo: `IVA ${x[1]}%` };
  if ((x = m.match(/^Los (\d+) ítems suman el /))) return { ...v, titulo: `Los ${x[1]} ítems suman bien` };
  if ((x = m.match(/^Fecha (\S+?)\.?$/))) return { ...v, titulo: `Fecha ${x[1]}` };
  // Lo demás: hasta el primer «:» o «(», si queda corto.
  const corte = m.split(/[:(]/)[0].replace(/\.$/, '').trim();
  return corte.length >= 6 && corte.length < m.length && corte.length <= 60 ? { ...v, titulo: corte } : v;
}
