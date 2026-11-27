import { Component, ElementRef, computed, effect, input, signal, viewChild } from '@angular/core';


const ZOOM_MIN = 0.25;
const ZOOM_MAX = 8;
const PASO = 1.25;

/**
 * La imagen del comprobante con zoom (botones, rueda del mouse o + / −), arrastre
 * para moverla, rotación de 90° y «100%» para volver al principio. Pensado para
 * mirar de cerca números borrosos (el 0 tachado y el 8 de las ticketeadoras). Los botones
 * están en la barra del panel (app-vista-archivo), que llama a estos métodos.
 *
 * La imagen arranca a todo el ancho y arriba; el zoom con la rueda se hace hacia
 * donde está el mouse. Con el teclado (el visor enfocado): + y − acercan y alejan,
 * las flechas mueven, 0 restablece y R rota.
 */
@Component({
  selector: 'app-visor-imagen',
  styles: `
    :host { display: block; position: relative; height: 100%; background: #f1f5f9; }
    .vi-marco { position: relative; height: 100%; overflow: hidden; touch-action: none; cursor: grab; outline: none; }
    .vi-marco:focus-visible { box-shadow: inset 0 0 0 2px #1d4ed8; }
    .vi-marco.arrastrando { cursor: grabbing; }
    .vi-marco img { display: block; width: 100%; height: auto; transform-origin: center center; user-select: none; -webkit-user-drag: none; will-change: transform; }
  `,
  template: `
    <div
      #marco
      class="vi-marco"
      [class.arrastrando]="arrastrando()"
      tabindex="0"
      role="img"
      [attr.aria-label]="alt() + '. Rueda del mouse o + y − para el zoom; arrastrá o usá las flechas para moverla.'"
      (wheel)="rueda($event)"
      (pointerdown)="empezar($event)"
      (pointermove)="mover($event)"
      (pointerup)="soltar($event)"
      (pointercancel)="soltar($event)"
      (keydown)="tecla($event)"
      (dblclick)="restablecer()"
    >
      <img #imagen [src]="src()" alt="" draggable="false" [style.transform]="transformacion()" />
    </div>
  `,
})
export class VisorImagen {
  readonly src = input.required<string>();
  readonly alt = input('Imagen del comprobante');

  readonly zoomMin = ZOOM_MIN;
  readonly zoomMax = ZOOM_MAX;
  readonly zoom = signal(1);
  protected readonly giro = signal(0);
  private readonly x = signal(0);
  private readonly y = signal(0);
  protected readonly arrastrando = signal(false);
  private inicio: { px: number; py: number; x: number; y: number; id: number } | null = null;

  private readonly marco = viewChild.required<ElementRef<HTMLDivElement>>('marco');
  private readonly imagen = viewChild.required<ElementRef<HTMLImageElement>>('imagen');

  readonly porcentaje = computed(() => `${Math.round(this.zoom() * 100)}%`);
  protected readonly transformacion = computed(
    () => `translate(${this.x()}px, ${this.y()}px) scale(${this.zoom()}) rotate(${this.giro()}deg)`,
  );

  constructor() {
    // Otra imagen: vuelve al principio.
    effect(() => {
      this.src();
      this.restablecer();
    });
  }

  acercar(): void {
    this.zoomHacia(this.zoom() * PASO);
  }

  alejar(): void {
    this.zoomHacia(this.zoom() / PASO);
  }

  restablecer(): void {
    this.zoom.set(1);
    this.giro.set(0);
    this.x.set(0);
    this.y.set(0);
  }

  rotar(): void {
    this.giro.update((g) => (g + 90) % 360);
  }

  /**
   * Cambia el zoom dejando quieto el punto (px, py) del marco (por defecto, el
   * centro de lo que se ve). La imagen escala y rota alrededor de su centro.
   */
  private zoomHacia(nuevo: number, px?: number, py?: number): void {
    const z = this.zoom();
    const z2 = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, nuevo));
    if (z2 === z) return;
    const img = this.imagen().nativeElement;
    const marco = this.marco().nativeElement;
    // Centro de la imagen sin transformar (en coordenadas del marco).
    const cx = img.offsetLeft + img.offsetWidth / 2;
    const cy = img.offsetTop + img.offsetHeight / 2;
    const ax = (px ?? marco.clientWidth / 2) - cx;
    const ay = (py ?? marco.clientHeight / 2) - cy;
    this.x.set(ax - ((ax - this.x()) * z2) / z);
    this.y.set(ay - ((ay - this.y()) * z2) / z);
    this.zoom.set(z2);
  }

  protected rueda(e: WheelEvent): void {
    e.preventDefault();
    const r = this.marco().nativeElement.getBoundingClientRect();
    const factor = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 0.05 : 0.0015));
    this.zoomHacia(this.zoom() * factor, e.clientX - r.left, e.clientY - r.top);
  }

  protected empezar(e: PointerEvent): void {
    if (e.button !== 0) return;
    this.inicio = { px: e.clientX, py: e.clientY, x: this.x(), y: this.y(), id: e.pointerId };
    this.marco().nativeElement.setPointerCapture(e.pointerId);
    this.arrastrando.set(true);
  }

  protected mover(e: PointerEvent): void {
    if (!this.inicio || e.pointerId !== this.inicio.id) return;
    this.x.set(this.inicio.x + e.clientX - this.inicio.px);
    this.y.set(this.inicio.y + e.clientY - this.inicio.py);
  }

  protected soltar(e: PointerEvent): void {
    if (!this.inicio || e.pointerId !== this.inicio.id) return;
    this.marco().nativeElement.releasePointerCapture(e.pointerId);
    this.inicio = null;
    this.arrastrando.set(false);
  }

  protected tecla(e: KeyboardEvent): void {
    const paso = e.shiftKey ? 120 : 40;
    const acciones: Record<string, () => void> = {
      '+': () => this.acercar(),
      '=': () => this.acercar(),
      '-': () => this.alejar(),
      '0': () => this.restablecer(),
      r: () => this.rotar(),
      R: () => this.rotar(),
      ArrowLeft: () => this.x.update((v) => v + paso),
      ArrowRight: () => this.x.update((v) => v - paso),
      ArrowUp: () => this.y.update((v) => v + paso),
      ArrowDown: () => this.y.update((v) => v - paso),
    };
    const accion = acciones[e.key];
    if (!accion || e.ctrlKey || e.metaKey || e.altKey) return;
    e.preventDefault();
    accion();
  }
}
