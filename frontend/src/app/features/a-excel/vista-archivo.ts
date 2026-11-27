import { ChangeDetectionStrategy, Component, ElementRef, afterNextRender, computed, effect, inject, input, signal, viewChild } from '@angular/core';
import { DomSanitizer } from '@angular/platform-browser';
import { VisorImagen } from './visor-imagen';

/**
 * El archivo original (foto o PDF) al lado de lo leído, con zoom y pantalla completa.
 * El PDF va con el visor del navegador; las fotos, con el visor de zoom (rueda hacia el
 * mouse, arrastre y teclado: + − flechas 0 R).
 */
@Component({
  selector: 'app-vista-archivo',
  imports: [VisorImagen],
  changeDetection: ChangeDetectionStrategy.OnPush,
  styles: `
    :host { display: block; }
    @media (min-width: 1001px) { :host { position: sticky; top: 12px; } }
    /* Un <dialog>: abierto en el lugar; en pantalla completa, modal (por encima del encabezado y de todo). */
    .va { position: static; margin: 0; padding: 0; width: 100%; max-width: none; max-height: none; color: inherit; border: 1px solid var(--color-borde); border-radius: 10px; background: #f1f5f9; overflow: hidden; }
    .va[open] { display: flex; flex-direction: column; }
    .va:modal { position: fixed; inset: 16px; width: auto; height: auto; box-shadow: 0 20px 60px rgba(15, 23, 42, .45); }
    .va::backdrop { background: rgba(15, 23, 42, .6); }
    .va-barra { display: flex; align-items: center; gap: 4px; padding: 6px 8px; background: #fff; border-bottom: 1px solid var(--color-borde); font-size: .8rem; }
    .va-barra button { min-width: 30px; height: 28px; padding: 0 8px; border: 1px solid var(--color-borde); border-radius: 6px; background: #fff; cursor: pointer; font-weight: 600; }
    .va-barra button:hover { background: #f1f5f9; }
    .va-barra .pct { min-width: 46px; text-align: center; font-variant-numeric: tabular-nums; color: #475569; }
    .va-barra .sep { flex: 1; }
    .va-foto { display: block; height: calc(100vh - 180px); min-height: 320px; }
    @media (max-width: 1000px) { .va-foto { height: 60vh; } }
    .va:modal .va-foto { height: auto; flex: 1; }
    iframe { display: block; width: 100%; height: calc(100vh - 200px); border: 0; }
    .va:modal iframe { flex: 1; height: auto; }
  `,
  template: `
    <dialog #dlg class="va" [attr.aria-label]="'El archivo original: ' + nombre()" (close)="alCerrar()" (click)="fuera($event)">
      <div class="va-barra">
        @if (foto(); as f) {
          <button type="button" (click)="f.alejar()" [disabled]="f.zoom() <= f.zoomMin" aria-label="Alejar" title="Alejar (−)">−</button>
          <span class="pct" aria-live="polite">{{ f.porcentaje() }}</span>
          <button type="button" (click)="f.acercar()" [disabled]="f.zoom() >= f.zoomMax" aria-label="Acercar" title="Acercar (+)">+</button>
          <button type="button" (click)="f.restablecer()" title="La foto entera a la vista (0)">Ajustar</button>
          <button type="button" (click)="f.rotar()" aria-label="Rotar 90 grados" title="Rotar 90° (R)">⟳ 90°</button>
        } @else {
          <button type="button" (click)="cambiar(-25)" [disabled]="zoom() <= 25" aria-label="Alejar">−</button>
          <span class="pct" aria-live="polite">{{ zoom() }}%</span>
          <button type="button" (click)="cambiar(25)" [disabled]="zoom() >= 400" aria-label="Acercar">+</button>
          <button type="button" (click)="zoom.set(100)" title="La hoja entera a la vista">Ajustar</button>
        }
        <span class="sep"></span>
        <button type="button" (click)="grande.set(!grande())" [attr.aria-pressed]="grande()">{{ grande() ? 'Cerrar' : 'Pantalla completa' }}</button>
      </div>
      @if (tipo() === 'imagen') {
        <app-visor-imagen class="va-foto" [src]="url()" [alt]="nombre()" />
      } @else {
        <iframe [src]="pdf()" [title]="nombre()"></iframe>
      }
    </dialog>
  `,
})
export class VistaArchivo {
  private readonly sanitizer = inject(DomSanitizer);
  readonly url = input.required<string>();
  readonly tipo = input.required<'imagen' | 'pdf'>();
  readonly nombre = input('');

  protected readonly zoom = signal(100);
  protected readonly grande = signal(false);
  protected readonly foto = viewChild(VisorImagen);

  /** El visor de PDF del navegador toma el zoom del «#zoom=». */
  protected readonly pdf = computed(() => this.sanitizer.bypassSecurityTrustResourceUrl(`${this.url()}#zoom=${this.zoom() === 100 ? 'page-width' : this.zoom()}`));

  constructor() {
    afterNextRender(() => this.abrir(this.grande()));
    effect(() => {
      const grande = this.grande();
      if (this.dlg().nativeElement.isConnected) this.abrir(grande);
    });
    // Archivo nuevo: vuelve al ancho del panel (la foto la reinicia su visor).
    effect(() => {
      this.url();
      this.zoom.set(100);
    });
  }

  private readonly dlg = viewChild.required<ElementRef<HTMLDialogElement>>('dlg');

  /** Abre el diálogo en el lugar o como modal (hay que cerrarlo para cambiar de modo). */
  private abrir(modal: boolean): void {
    const d = this.dlg().nativeElement;
    if (d.open && d.matches(':modal') === modal) return;
    if (d.open) d.close();
    if (modal) d.showModal();
    else d.show();
  }

  /** Esc o «Cerrar» en pantalla completa: vuelve al lugar. */
  protected alCerrar(): void {
    // El «close» llega después: si ya se volvió a abrir (cambio de modo), no es un cierre.
    if (this.dlg().nativeElement.open) return;
    this.grande.set(false);
    this.abrir(false);
  }

  /** Clic en el fondo oscuro. */
  protected fuera(ev: MouseEvent): void {
    if (this.grande() && ev.target === this.dlg().nativeElement) this.grande.set(false);
  }

  protected cambiar(paso: number): void {
    this.zoom.update((z) => Math.min(400, Math.max(25, z + paso)));
  }
}
