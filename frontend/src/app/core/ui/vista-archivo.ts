import { Component, ElementRef, computed, effect, input, output, signal, viewChild } from '@angular/core';

export interface HojaVista {
  nombre: string;
  filas: string[][];
  total_filas: number;
  cortado: boolean;
  /** Formato de las celdas que lo tienen, por «fila,columna» (desde 0). */
  formatos?: Record<string, FormatoCelda>;
}

export interface FormatoCelda {
  fondo?: string;
  color?: string;
  negrita?: boolean;
}

export interface VistaDeArchivo {
  nombre: string;
  hojas: HojaVista[];
}

/**
 * El ojo de «Archivos»: el Excel o el CSV con aspecto de planilla, sin
 * descargarlo. Columnas A, B, C…, filas numeradas, barra de fórmulas y una
 * pestaña por hoja abajo.
 */
@Component({
  selector: 'app-vista-archivo',
  styles: `
    .va { width: min(1200px, calc(100vw - 2rem)); max-height: calc(100vh - 2rem); overflow: hidden; padding: 0; }
    .va-caja { display: flex; flex-direction: column; height: min(760px, calc(100vh - 2rem - 2px)); background: #fff; }
    /* Barra verde de título, como Excel. */
    .va-cab { display: flex; align-items: center; gap: 10px; padding: 8px 10px 8px 14px; background: #217346; color: #fff; }
    .va-cab h2 { margin: 0; font-size: .9rem; font-weight: 600; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
    .va-cerrar { border: 0; background: transparent; color: #fff; font: inherit; font-size: .85rem; padding: 4px 10px; border-radius: 4px; cursor: pointer; }
    .va-cerrar:hover, .va-cerrar:focus-visible { background: rgba(255, 255, 255, .18); outline: none; }
    /* Barra de fórmulas: celda elegida y su contenido. */
    .va-formula { display: flex; align-items: stretch; border-bottom: 1px solid #d4d4d4; font-size: .8rem; background: #fff; }
    .va-ref { width: 84px; padding: 5px 8px; border-right: 1px solid #d4d4d4; color: #333; text-align: center; font-variant-numeric: tabular-nums; }
    .va-fx { padding: 5px 10px; color: #777; font-style: italic; border-right: 1px solid #e5e5e5; }
    .va-valor { padding: 5px 10px; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: #222; }
    .va-tabla { overflow: auto; flex: 1; min-height: 200px; background: #fff; }
    table { border-collapse: separate; border-spacing: 0; font-family: Calibri, 'Segoe UI', Arial, sans-serif; font-size: 13px; color: #222; font-variant-numeric: tabular-nums; }
    td { border-right: 1px solid #e1e1e1; border-bottom: 1px solid #e1e1e1; padding: 2px 6px; height: 20px; white-space: nowrap; min-width: 64px; max-width: 320px; overflow: hidden; text-overflow: ellipsis; text-align: left; cursor: cell; }
    td.num { text-align: right; }
    tbody tr:first-child td:not(.va-n) { font-weight: 700; }
    td.sel { outline: 2px solid #217346; outline-offset: -2px; }
    /* Encabezados de fila y columna, grises como en Excel. */
    thead th { position: sticky; top: 0; z-index: 1; background: #f3f3f3; color: #444; font-weight: 400; font-size: 12px; text-align: center; padding: 2px 6px; border-right: 1px solid #d4d4d4; border-bottom: 1px solid #c8c8c8; }
    thead th.col-sel { background: #d3f0e0; color: #185c37; box-shadow: inset 0 -2px 0 #217346; }
    .va-n { position: sticky; left: 0; z-index: 2; background: #f3f3f3; color: #444; text-align: center; font-size: 12px; min-width: 42px; border-right: 1px solid #c8c8c8; border-bottom: 1px solid #d4d4d4; cursor: default; }
    tr.fila-sel .va-n { background: #d3f0e0; color: #185c37; box-shadow: inset -2px 0 0 #217346; }
    thead .va-n { z-index: 3; }
    /* Pestañas de hojas abajo, como en Excel. */
    .va-pie { display: flex; align-items: stretch; border-top: 1px solid #c8c8c8; background: #f3f3f3; font-size: .8rem; min-height: 32px; }
    .va-hojas { display: flex; overflow-x: auto; }
    .va-hojas button { border: 0; border-right: 1px solid #d4d4d4; background: transparent; padding: 6px 16px; cursor: pointer; font: inherit; color: #444; white-space: nowrap; }
    .va-hojas button:hover { background: #e8e8e8; }
    .va-hojas button[aria-selected='true'] { background: #fff; color: #217346; font-weight: 700; box-shadow: inset 0 -3px 0 #217346; }
    .va-cuenta { margin-left: auto; padding: 7px 14px; color: #666; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .va-vacio { padding: 40px; text-align: center; color: #666; flex: 1; margin: 0; }
  `,
  template: `
    <dialog #dialogo class="mf-dialogo va" (close)="cerrado.emit()" (click)="alClicFuera($event)" aria-labelledby="va-titulo">
      <div class="va-caja">
        <div class="va-cab">
          <svg class="h-5 w-5 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2" /><path d="M3 9h18M3 15h18M9 3v18" /></svg>
          <h2 id="va-titulo" [title]="nombre()">{{ nombre() }}</h2>
          <button type="button" class="va-cerrar" (click)="cerrar()">Cerrar ✕</button>
        </div>
        @if (cargando()) {
          <p class="va-vacio">Abriendo el archivo…</p>
        } @else if (vista(); as v) {
          @if (actual(); as h) {
            @if (h.filas.length) {
              <div class="va-formula">
                <span class="va-ref" aria-label="Celda">{{ referencia() }}</span>
                <span class="va-fx" aria-hidden="true">fx</span>
                <span class="va-valor" [title]="valorElegido()">{{ valorElegido() }}</span>
              </div>
              <div class="va-tabla" tabindex="0" [attr.aria-label]="'Hoja ' + h.nombre">
                <table>
                  <thead>
                    <tr>
                      <th class="va-n" scope="col"></th>
                      @for (l of letras(); track $index) {
                        <th scope="col" [class.col-sel]="celda()?.c === $index">{{ l }}</th>
                      }
                    </tr>
                  </thead>
                  <tbody>
                    @for (f of h.filas; track $index; let i = $index) {
                      <tr [class.fila-sel]="celda()?.f === i">
                        <td class="va-n">{{ i + 1 }}</td>
                        @for (c of f; track $index; let j = $index) {
                          <td [class.num]="esNumero(c)" [style.background]="h.formatos?.[i + ',' + j]?.fondo" [style.color]="h.formatos?.[i + ',' + j]?.color" [style.font-weight]="h.formatos?.[i + ',' + j]?.negrita ? 700 : null" [class.sel]="celda()?.f === i && celda()?.c === j" [title]="c" (click)="celda.set({ f: i, c: j })">{{ c }}</td>
                        }
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            } @else {
              <p class="va-vacio">Esta hoja está vacía.</p>
            }
          }
          <div class="va-pie">
            <div class="va-hojas" role="tablist">
              @for (hj of v.hojas; track $index) {
                <button type="button" role="tab" [attr.aria-selected]="$index === hoja()" (click)="elegirHoja($index)">{{ hj.nombre }}</button>
              }
            </div>
            @if (actual(); as h) {
              <span class="va-cuenta">
                {{ h.total_filas }} {{ h.total_filas === 1 ? 'fila' : 'filas' }}{{ h.cortado ? ' · se muestran las primeras ' + h.filas.length + ': descargalo para ver todo' : '' }}
              </span>
            }
          </div>
        }
      </div>
    </dialog>
  `,
})
export class VistaArchivo {
  /** El archivo a mostrar; null cierra la ventana. */
  readonly vista = input<VistaDeArchivo | null>(null);
  readonly nombre = input('');
  readonly cargando = input(false);
  readonly abierta = input(false);
  readonly cerrado = output<void>();

  protected readonly hoja = signal(0);
  protected readonly actual = computed(() => this.vista()?.hojas[this.hoja()] ?? null);
  /** Celda elegida (fila y columna desde 0), como el cuadro verde de Excel. */
  protected readonly celda = signal<{ f: number; c: number } | null>(null);
  protected readonly letras = computed(() => {
    const ancho = Math.max(0, ...(this.actual()?.filas.map((f) => f.length) ?? [0]));
    return Array.from({ length: ancho }, (_, i) => letraDeColumna(i));
  });
  protected readonly referencia = computed(() => {
    const c = this.celda();
    return c ? letraDeColumna(c.c) + (c.f + 1) : '';
  });
  protected readonly valorElegido = computed(() => {
    const c = this.celda();
    return c ? (this.actual()?.filas[c.f]?.[c.c] ?? '') : '';
  });
  private readonly dialogo = viewChild.required<ElementRef<HTMLDialogElement>>('dialogo');

  constructor() {
    effect(() => {
      const d = this.dialogo().nativeElement;
      if (this.abierta() && !d.open) d.showModal();
      if (!this.abierta() && d.open) d.close();
    });
    // Archivo nuevo: arranca en la primera hoja, sin celda elegida.
    effect(() => {
      this.vista();
      this.hoja.set(0);
      this.celda.set(null);
    });
  }

  protected elegirHoja(indice: number): void {
    this.hoja.set(indice);
    this.celda.set(null);
  }

  /** Importes, cantidades y fechas van a la derecha, como los alinea Excel. */
  protected esNumero(texto: string): boolean {
    return /^-?\$?\s?[\d.]+(,\d+)?%?$/.test(texto) || /^\d{2}\/\d{2}\/\d{4}( \d{2}:\d{2})?$/.test(texto);
  }

  protected cerrar(): void {
    this.dialogo().nativeElement.close();
  }

  protected alClicFuera(evento: MouseEvent): void {
    if (evento.target === this.dialogo().nativeElement) this.cerrar();
  }
}

/** 0 → A, 25 → Z, 26 → AA… */
export function letraDeColumna(indice: number): string {
  let s = '';
  for (let n = indice + 1; n > 0; n = Math.floor((n - 1) / 26)) s = String.fromCharCode(65 + ((n - 1) % 26)) + s;
  return s;
}
