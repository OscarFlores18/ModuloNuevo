import { AfterViewInit, DestroyRef, Directive, ElementRef, inject } from '@angular/core';
import { NgControl } from '@angular/forms';

/**
 * Hace que un `<input type="month">` crezca con el texto que muestra
 * («mayo de 2026» es más corto que «septiembre de 2026»).
 *
 * Chrome no aplica `field-sizing: content` a los campos de fecha, así que se mide
 * el texto con la misma fuente del campo (canvas) y se le suma el espacio del
 * relleno, el borde y el ícono del calendario. El texto se arma con `Intl`, igual
 * que lo muestra el navegador en el idioma del sistema.
 */
@Directive({ selector: 'input[type=month][appAnchoAjustable]' })
export class AnchoAjustable implements AfterViewInit {
  private readonly el = inject<ElementRef<HTMLInputElement>>(ElementRef).nativeElement;
  private readonly control = inject(NgControl, { optional: true, self: true });
  private readonly destroyRef = inject(DestroyRef);

  /** Ícono del calendario + aire alrededor del texto. */
  private static readonly EXTRA_PX = 34;
  private static canvas: HTMLCanvasElement | null = null;

  ngAfterViewInit(): void {
    this.ajustar();
    const sub = this.control?.valueChanges?.subscribe(() => this.ajustar());
    const alCambiar = () => this.ajustar();
    this.el.addEventListener('input', alCambiar);
    this.destroyRef.onDestroy(() => {
      sub?.unsubscribe();
      this.el.removeEventListener('input', alCambiar);
    });
  }

  private textoVisible(): string {
    const m = /^(\d{4})-(\d{2})$/.exec(this.el.value);
    // Vacío, Chrome muestra «---------- de ----».
    if (!m) return '---------- de ----';
    const fecha = new Date(Number(m[1]), Number(m[2]) - 1, 1);
    return new Intl.DateTimeFormat(navigator.language || 'es-AR', { month: 'long', year: 'numeric' }).format(fecha);
  }

  private ajustar(): void {
    const estilo = getComputedStyle(this.el);
    const canvas = (AnchoAjustable.canvas ??= document.createElement('canvas'));
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.font = `${estilo.fontWeight} ${estilo.fontSize} ${estilo.fontFamily}`;
    const texto = Math.ceil(ctx.measureText(this.textoVisible()).width);
    const marco =
      parseFloat(estilo.paddingLeft) + parseFloat(estilo.paddingRight) + parseFloat(estilo.borderLeftWidth) + parseFloat(estilo.borderRightWidth);
    this.el.style.width = `${texto + marco + AnchoAjustable.EXTRA_PX}px`;
  }
}
