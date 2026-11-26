import { ChangeDetectionStrategy, Component, HostListener, computed, effect, inject, input, signal } from '@angular/core';
import { DomSanitizer } from '@angular/platform-browser';

/** El archivo original (foto o PDF) al lado de lo leído, con zoom y pantalla completa. */
@Component({
  selector: 'app-vista-archivo',
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; position: sticky; top: 12px; }
    .va { border: 1px solid var(--color-borde); border-radius: 10px; background: #f1f5f9; overflow: hidden; display: flex; flex-direction: column; }
    .va.grande { position: fixed; inset: 16px; z-index: 60; box-shadow: 0 20px 60px rgba(15, 23, 42, .45); }
    .va-fondo { position: fixed; inset: 0; z-index: 59; background: rgba(15, 23, 42, .55); }
    .va-barra { display: flex; align-items: center; gap: 4px; padding: 6px 8px; background: #fff; border-bottom: 1px solid var(--color-borde); font-size: .8rem; }
    .va-barra button { min-width: 30px; height: 28px; padding: 0 8px; border: 1px solid var(--color-borde); border-radius: 6px; background: #fff; cursor: pointer; font-weight: 600; }
    .va-barra button:hover { background: #f1f5f9; }
    .va-barra .pct { min-width: 46px; text-align: center; font-variant-numeric: tabular-nums; color: #475569; }
    .va-barra .sep { flex: 1; }
    .va-lienzo { overflow: auto; max-height: calc(100vh - 180px); cursor: zoom-in; }
    .va.grande .va-lienzo { max-height: none; flex: 1; }
    .va-lienzo.arrastrando { cursor: grabbing; }
    .va-lienzo img { display: block; height: auto; max-width: none; user-select: none; }
    .va-lienzo img.entera { width: auto !important; max-width: 100%; max-height: calc(100vh - 190px); margin: 0 auto; }
    .va.grande .va-lienzo img.entera { max-height: calc(100vh - 90px); }
    iframe { display: block; width: 100%; height: calc(100vh - 200px); border: 0; }
    .va.grande iframe { height: 100%; }
  `,
  template: `
    @if (grande()) { <div class="va-fondo" (click)="grande.set(false)" aria-hidden="true"></div> }
    <div class="va" [class.grande]="grande()" role="region" [attr.aria-label]="'El archivo original: ' + nombre()">
      <div class="va-barra">
        <button type="button" (click)="cambiar(-25)" [disabled]="zoom() <= 25" aria-label="Alejar">−</button>
        <span class="pct" aria-live="polite">{{ zoom() }}%</span>
        <button type="button" (click)="cambiar(25)" [disabled]="zoom() >= 400" aria-label="Acercar">+</button>
        <button type="button" (click)="zoom.set(100)" title="La hoja entera a la vista">Ajustar</button>
        <span class="sep"></span>
        <button type="button" (click)="grande.set(!grande())" [attr.aria-pressed]="grande()">{{ grande() ? 'Cerrar' : 'Pantalla completa' }}</button>
      </div>
      @if (tipo() === 'imagen') {
        <div
          class="va-lienzo"
          [class.arrastrando]="arrastre !== null"
          (wheel)="rueda($event)"
          (dblclick)="zoom.set(zoom() === 100 ? 200 : 100)"
          (pointerdown)="empezar($event)"
          (pointermove)="mover($event)"
          (pointerup)="arrastre = null"
          (pointerleave)="arrastre = null"
          title="Doble clic o Ctrl + rueda para hacer zoom; arrastrá para moverte"
        >
          <img [src]="url()" [alt]="nombre()" [class.entera]="zoom() === 100" [style.width.%]="zoom()" draggable="false" />
        </div>
      } @else {
        <iframe [src]="pdf()" [title]="nombre()"></iframe>
      }
    </div>
  `,
})
export class VistaArchivo {
  private readonly sanitizer = inject(DomSanitizer);
  readonly url = input.required<string>();
  readonly tipo = input.required<'imagen' | 'pdf'>();
  readonly nombre = input('');

  protected readonly zoom = signal(100);
  protected readonly grande = signal(false);
  protected arrastre: { x: number; y: number; izq: number; arriba: number } | null = null;

  /** El visor de PDF del navegador toma el zoom del «#zoom=». */
  protected readonly pdf = computed(() => this.sanitizer.bypassSecurityTrustResourceUrl(`${this.url()}#zoom=${this.zoom() === 100 ? 'page-width' : this.zoom()}`));

  constructor() {
    // Archivo nuevo: vuelve al ancho del panel.
    effect(() => {
      this.url();
      this.zoom.set(100);
    });
  }

  @HostListener('document:keydown.escape')
  protected cerrar(): void {
    this.grande.set(false);
  }

  protected cambiar(paso: number): void {
    this.zoom.update((z) => Math.min(400, Math.max(25, z + paso)));
  }

  protected rueda(ev: WheelEvent): void {
    if (!ev.ctrlKey) return;
    ev.preventDefault();
    this.cambiar(ev.deltaY < 0 ? 25 : -25);
  }

  protected empezar(ev: PointerEvent): void {
    const el = ev.currentTarget as HTMLElement;
    if (this.zoom() <= 100) return;
    this.arrastre = { x: ev.clientX, y: ev.clientY, izq: el.scrollLeft, arriba: el.scrollTop };
  }

  protected mover(ev: PointerEvent): void {
    if (!this.arrastre) return;
    const el = ev.currentTarget as HTMLElement;
    el.scrollLeft = this.arrastre.izq - (ev.clientX - this.arrastre.x);
    el.scrollTop = this.arrastre.arriba - (ev.clientY - this.arrastre.y);
  }
}
