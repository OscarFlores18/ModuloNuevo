import { Component, ElementRef, effect, input, model, output, viewChild } from '@angular/core';

/**
 * Confirmación antes de una acción que no se deshace sola. Usa <dialog> nativo:
 * atrapa el foco, cierra con Esc y deja el resto de la página inerte.
 *
 *   <app-confirm-modal [(abierto)]="confirmar" titulo="…" (confirmado)="hacer()">Detalle</app-confirm-modal>
 */
@Component({
  selector: 'app-confirm-modal',
  template: `
    <dialog #dialogo (close)="abierto.set(false)" (click)="alClicFuera($event)" [attr.aria-labelledby]="id + '-titulo'"
      class="mf-dialogo w-[min(28rem,calc(100vw-2rem))]" [class.border-l-4]="true" [style.border-left-color]="peligro() ? 'var(--color-error)' : 'var(--color-acento)'">
      <div class="p-5">
        <h2 [id]="id + '-titulo'" class="m-0 text-lg font-semibold">{{ titulo() }}</h2>
        <div class="mf-tenue mt-2 text-sm"><ng-content /></div>
        <div class="mt-5 flex justify-end gap-2">
          <!-- El foco arranca en Cancelar: Enter por error no ejecuta la acción -->
          <button type="button" autofocus (click)="abierto.set(false)" class="mf-btn mf-btn-suave">{{ textoCancelar() }}</button>
          <button type="button" (click)="confirmar()" class="mf-btn" [class.mf-btn-peligro]="peligro()">{{ textoConfirmar() }}</button>
        </div>
      </div>
    </dialog>
  `,
})
export class ConfirmModal {
  private static siguiente = 0;
  protected readonly id = `confirmar-${ConfirmModal.siguiente++}`;

  readonly abierto = model(false);
  readonly titulo = input.required<string>();
  readonly textoConfirmar = input('Confirmar');
  readonly textoCancelar = input('Cancelar');
  /** Rojo: la acción corta o borra algo. */
  readonly peligro = input(false);
  readonly confirmado = output<void>();

  private readonly dialogo = viewChild.required<ElementRef<HTMLDialogElement>>('dialogo');

  constructor() {
    effect(() => {
      const d = this.dialogo().nativeElement;
      if (this.abierto() && !d.open) d.showModal();
      if (!this.abierto() && d.open) d.close();
    });
  }

  protected confirmar(): void {
    this.abierto.set(false);
    this.confirmado.emit();
  }

  /** Un clic en el fondo oscuro (fuera de la caja) cancela. */
  protected alClicFuera(evento: MouseEvent): void {
    if (evento.target === this.dialogo().nativeElement) this.abierto.set(false);
  }
}
