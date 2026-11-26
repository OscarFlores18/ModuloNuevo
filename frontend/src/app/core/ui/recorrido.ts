import { Component, DestroyRef, effect, inject, signal, untracked } from '@angular/core';
import { RecorridoService } from './recorrido.service';

interface Caja {
  top: number;
  left: number;
  width: number;
  height: number;
}

const MARGEN = 8;

/**
 * El recorrido en pantalla: oscurece todo menos lo que se explica y pone al
 * lado un globo con «Anterior», «Siguiente» y «Saltar». Flechas y Esc también
 * sirven.
 */
@Component({
  selector: 'app-recorrido',
  host: {
    '(window:resize)': 'medir()',
    '(document:keydown)': 'alTeclado($event)',
  },
  template: `
    @if (recorrido.actual(); as a) {
      @let paso = a.pasos[a.indice];
      <div class="cf-recorrido-fondo" (click)="recorrido.terminar()" aria-hidden="true"></div>
      @if (foco(); as f) {
        <div class="cf-recorrido-foco" [style.top.px]="f.top" [style.left.px]="f.left" [style.width.px]="f.width" [style.height.px]="f.height"></div>
      }
      <div class="cf-recorrido-globo" role="dialog" aria-modal="true" [attr.aria-label]="paso.titulo" [style.top.px]="globo().top" [style.left.px]="globo().left">
        <h3>{{ paso.titulo }}</h3>
        <p>{{ paso.texto }}</p>
        <div class="cf-recorrido-pie">
          <span class="cf-recorrido-puntos" [attr.aria-label]="'Paso ' + (a.indice + 1) + ' de ' + a.pasos.length">
            @for (p of a.pasos; track $index) { <span [class.activo]="$index === a.indice"></span> }
          </span>
          <button type="button" class="mf-btn-link text-xs" (click)="recorrido.terminar()">Saltar</button>
          @if (a.indice > 0) {
            <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="recorrido.anterior()">Anterior</button>
          }
          <button type="button" class="mf-btn mf-btn-chico" (click)="recorrido.siguiente()" autofocus>
            {{ a.indice + 1 < a.pasos.length ? 'Siguiente' : 'Listo' }}
          </button>
        </div>
      </div>
    }
  `,
})
export class Recorrido {
  protected readonly recorrido = inject(RecorridoService);
  protected readonly foco = signal<Caja | null>(null);
  protected readonly globo = signal<{ top: number; left: number }>({ top: 0, left: 0 });
  private cuadro = 0;

  constructor() {
    // Cada paso nuevo: se lleva el elemento a la vista y se mide.
    effect(() => {
      const a = this.recorrido.actual();
      untracked(() => {
        if (!a) {
          this.foco.set(null);
          return;
        }
        const el = this.recorrido.elemento(a.pasos[a.indice]);
        if (el) this.llevarALaVista(el);
        this.medir();
        // El scroll suave tarda: se vuelve a medir cuando termina.
        setTimeout(() => this.medir(), 350);
      });
    });
    // La página se mueve dentro de su panel (no la ventana): se escucha cualquier scroll.
    const alMover = () => this.medir();
    document.addEventListener('scroll', alMover, { capture: true, passive: true });
    inject(DestroyRef).onDestroy(() => {
      cancelAnimationFrame(this.cuadro);
      document.removeEventListener('scroll', alMover, { capture: true });
    });
  }

  /**
   * Lo lleva a la vista debajo del encabezado fijo (scrollIntoView no lo tiene
   * en cuenta y lo dejaba tapado). Si ya se ve entero, no se mueve.
   */
  private llevarALaVista(el: HTMLElement): void {
    const encabezado = document.querySelector('.cf-header')?.getBoundingClientRect().bottom ?? 0;
    const r = el.getBoundingClientRect();
    const libre = window.innerHeight - encabezado;
    // Lugar para el globo debajo (o arriba) del elemento.
    const visible = r.top >= encabezado + MARGEN && r.bottom <= window.innerHeight - 200;
    if (visible) return;
    const arriba = r.height + 220 < libre ? Math.max(16, (libre - r.height - 200) / 2) : 16;
    const caja = contenedorConScroll(el);
    if (caja) {
      const desde = caja.getBoundingClientRect().top;
      const tope = Math.max(desde, encabezado);
      caja.scrollTo({ top: Math.max(0, caja.scrollTop + r.top - tope - arriba), behavior: 'smooth' });
    } else {
      window.scrollTo({ top: Math.max(0, window.scrollY + r.top - encabezado - arriba), behavior: 'smooth' });
    }
  }

  protected medir(): void {
    cancelAnimationFrame(this.cuadro);
    this.cuadro = requestAnimationFrame(() => {
      const a = this.recorrido.actual();
      if (!a) return;
      const el = this.recorrido.elemento(a.pasos[a.indice]);
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const ancho = Math.min(340, vw - 24);
      if (!el) {
        this.foco.set(null);
        this.globo.set({ top: vh / 2 - 90, left: (vw - ancho) / 2 });
        return;
      }
      const r = el.getBoundingClientRect();
      const caja = {
        top: Math.max(r.top - MARGEN, 4),
        left: Math.max(r.left - MARGEN, 4),
        width: Math.min(r.width + MARGEN * 2, vw - 8),
        height: Math.min(r.height + MARGEN * 2, vh - 8),
      };
      this.foco.set(caja);
      // El globo, debajo si entra; si no, arriba; si tampoco, dentro de la pantalla.
      const alto = 190;
      const abajo = caja.top + caja.height + 10;
      const arriba = caja.top - alto - 10;
      const enColumna = Math.min(Math.max(caja.left, 12), vw - ancho - 12);
      if (abajo + alto <= vh) {
        this.globo.set({ top: abajo, left: enColumna });
      } else if (arriba >= 8) {
        this.globo.set({ top: arriba, left: enColumna });
      } else {
        // Muy alto para ponerlo arriba o abajo: al costado, si hay lugar.
        const top = Math.min(Math.max(caja.top + 16, 8), vh - alto - 8);
        const derecha = caja.left + caja.width + 12;
        if (derecha + ancho <= vw - 8) this.globo.set({ top, left: derecha });
        else if (caja.left - ancho - 12 >= 8) this.globo.set({ top, left: caja.left - ancho - 12 });
        else this.globo.set({ top, left: enColumna });
      }
    });
  }

  protected alTeclado(ev: KeyboardEvent): void {
    if (!this.recorrido.actual()) return;
    if (ev.key === 'Escape') this.recorrido.terminar();
    else if (ev.key === 'ArrowRight') this.recorrido.siguiente();
    else if (ev.key === 'ArrowLeft') this.recorrido.anterior();
    else return;
    ev.preventDefault();
  }
}

/** El primer contenedor que se mueve con scroll (el panel principal), o null si es la ventana. */
function contenedorConScroll(el: HTMLElement): HTMLElement | null {
  for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY;
    if ((o === 'auto' || o === 'scroll') && p.scrollHeight > p.clientHeight + 1) return p;
  }
  return null;
}
