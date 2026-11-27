import { ChangeDetectionStrategy, Component, ElementRef, computed, effect, input, output, signal, viewChild } from '@angular/core';

export interface HojaVistaPrevia {
  nombre: string;
  filas: string[][];
  total: number;
  recortada: boolean;
}

export interface VistaPreviaArchivo {
  nombre: string;
  hojas: HojaVistaPrevia[];
  /** Si es un .zip: lo que trae adentro. */
  adentro?: string[];
}

/**
 * «Vista previa» de un archivo del control: sus hojas como tablas, en un <dialog>
 * modal (Esc o clic afuera lo cierran). La primera fila va como encabezado.
 *
 *   <app-vista-previa [vista]="v" [cargando]="…" [error]="…" (cerrar)="…" />
 */
@Component({
  selector: 'app-vista-previa',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    .vp { width: min(1200px, calc(100vw - 2rem)); height: min(85vh, 900px); }
    .vp[open] { display: flex; flex-direction: column; }
    .vp-cabeza { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-bottom: 1px solid var(--color-borde); }
    .vp-titulo { margin: 0; font-size: 1rem; font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
    .vp-hojas { display: flex; gap: 4px; padding: 8px 16px 0; flex-wrap: wrap; border-bottom: 1px solid var(--color-borde); }
    .vp-hojas button { padding: 6px 12px; border: 1px solid var(--color-borde); border-bottom: 0; border-radius: 8px 8px 0 0; background: transparent; color: inherit; cursor: pointer; font-size: .85rem; max-width: 22rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .vp-hojas button[aria-selected='true'] { background: var(--color-panel); font-weight: 600; box-shadow: inset 0 2px 0 var(--color-acento); }
    .vp-cuerpo { flex: 1; overflow: auto; }
    .vp-nota { padding: 8px 16px; font-size: .8rem; }
    table { border-collapse: collapse; font-size: .8rem; font-variant-numeric: tabular-nums; }
    th, td { border: 1px solid var(--color-borde); padding: 4px 8px; white-space: nowrap; max-width: 28rem; overflow: hidden; text-overflow: ellipsis; text-align: left; }
    thead th { position: sticky; top: 0; background: var(--color-fondo, #f1f5f9); z-index: 1; }
    td.n, th.n { position: sticky; left: 0; background: var(--color-fondo, #f1f5f9); color: #64748b; text-align: right; }
    .vp-vacio { padding: 32px 16px; text-align: center; }
  `,
  template: `
    <dialog #dlg class="mf-dialogo vp" aria-labelledby="vp-titulo" (close)="cerrar.emit()" (click)="fuera($event)">
      <div class="vp-cabeza">
        <h2 id="vp-titulo" class="vp-titulo" [title]="titulo()">Vista previa · {{ titulo() }}</h2>
        <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="cerrar.emit()" autofocus>Cerrar</button>
      </div>
      @if (cargando()) {
        <p class="mf-tenue vp-vacio m-0">Abriendo el archivo…</p>
      } @else if (error()) {
        <p class="vp-vacio m-0" role="alert">{{ error() }}</p>
      } @else if (vista(); as v) {
        @if (v.adentro?.length) {
          <p class="mf-tenue vp-nota m-0">Es un .zip; trae: {{ v.adentro!.join(', ') }}</p>
        }
        @if (v.hojas.length > 1) {
          <div class="vp-hojas" role="tablist" aria-label="Hojas">
            @for (h of v.hojas; track $index) {
              <button type="button" role="tab" [attr.aria-selected]="$index === elegida()" (click)="elegida.set($index)" [title]="h.nombre">{{ h.nombre }}</button>
            }
          </div>
        }
        @if (hoja(); as h) {
          <div class="vp-cuerpo" role="tabpanel">
            @if (h.filas.length) {
              <table>
                <thead>
                  <tr>
                    <th class="n">#</th>
                    @for (c of h.filas[0]; track $index) { <th [title]="c">{{ c }}</th> }
                  </tr>
                </thead>
                <tbody>
                  @for (f of h.filas.slice(1); track $index; let i = $index) {
                    <tr>
                      <td class="n">{{ i + 1 }}</td>
                      @for (c of f; track $index) { <td [title]="c">{{ c }}</td> }
                    </tr>
                  }
                </tbody>
              </table>
            } @else {
              <p class="mf-tenue vp-vacio m-0">La hoja está vacía.</p>
            }
          </div>
          <p class="mf-tenue vp-nota m-0">
            {{ h.total > 0 ? h.total - 1 : 0 }} fila(s) de datos{{ h.recortada ? ' · se muestran las primeras ' + (h.filas.length - 1) + ': descargalo para ver todo' : '' }}.
          </p>
        }
      }
    </dialog>
  `,
})
export class VistaPrevia {
  readonly titulo = input.required<string>();
  readonly vista = input<VistaPreviaArchivo | null>(null);
  readonly cargando = input(false);
  readonly error = input<string | null>(null);
  readonly cerrar = output<void>();

  protected readonly elegida = signal(0);
  protected readonly hoja = computed(() => this.vista()?.hojas[this.elegida()] ?? null);
  private readonly dlg = viewChild.required<ElementRef<HTMLDialogElement>>('dlg');

  constructor() {
    effect(() => {
      const d = this.dlg().nativeElement;
      if (!d.open) d.showModal();
    });
    // Archivo nuevo: vuelve a la primera hoja.
    effect(() => {
      this.vista();
      this.elegida.set(0);
    });
  }

  protected fuera(evento: MouseEvent): void {
    if (evento.target === this.dlg().nativeElement) this.cerrar.emit();
  }
}
