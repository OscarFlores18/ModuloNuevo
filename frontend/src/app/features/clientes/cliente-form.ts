import { Component, ElementRef, effect, inject, input, model, output, signal, untracked, viewChild } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { cuitValida, formatearCuit } from '../../core/cuit.pipe';
import { Cliente, ClienteEntrada, mensajeDeError } from '../../core/models';
import { ClientesStore } from '../../core/services/clientes.store';
import { Alerta } from '../../core/ui/alerta';
import { ConfirmModal } from '../../core/ui/confirm-modal';

/**
 * Alta y edición de un cliente. En pantallas anchas va en el panel de la derecha
 * (`enPanel`), al lado de la lista; en las chicas, en un <dialog>. La clave
 * fiscal no viaja al navegador: al editar, dejarla vacía mantiene la guardada.
 */
@Component({
  selector: 'app-cliente-form',
  imports: [Alerta, ConfirmModal, NgTemplateOutlet],
  template: `
    @if (enPanel()) {
      @if (abierto()) {
        <section class="mf-panel cl-form-panel" aria-labelledby="t-cliente-form">
          <ng-container *ngTemplateOutlet="contenido" />
        </section>
      }
    } @else {
      <dialog #dialogo (close)="abierto.set(false)" aria-labelledby="t-cliente-form" class="mf-dialogo w-[min(40rem,calc(100vw-2rem))]">
        <ng-container *ngTemplateOutlet="contenido" />
      </dialog>
    }

    <ng-template #contenido>
      <form (submit)="$event.preventDefault(); guardar()" novalidate>
        <header class="mf-panel-titulo !mb-0 border-b border-borde px-5 py-4">
          <div class="min-w-0 flex-1">
            <h2 id="t-cliente-form">{{ cliente() ? 'Editar cliente' : 'Nuevo cliente' }}</h2>
            <span class="mf-sub">Los datos con los que los bots entran a ARCA y a Xubio.</span>
          </div>
          <button type="button" class="mf-icono-btn" (click)="abierto.set(false)" aria-label="Cerrar">✕</button>
        </header>

        <div class="cl-form-cuerpo flex flex-col gap-4 overflow-y-auto px-5 py-4">
          <label class="mf-campo">
            <span>Nombre del cliente</span>
            <input class="mf-input" name="nombre" [value]="nombre()" (input)="nombre.set($any($event.target).value)" required
              [attr.aria-invalid]="intentado() && !nombre().trim()" placeholder="Como figura en Xubio (se usa para elegir la empresa)" />
            <small class="mf-ayuda">En Xubio se busca la empresa por este nombre. También es el nombre de la carpeta del cliente.</small>
          </label>

          <label class="mf-campo">
            <span>Email del cliente <span class="mf-tenue">(opcional)</span></span>
            <input class="mf-input" type="email" name="email" autocomplete="email" [value]="email()" (input)="email.set($any($event.target).value)"
              placeholder="contacto@empresa.com" [attr.aria-invalid]="emailInvalido()" />
            <small class="mf-ayuda">Para «Consultar al cliente»: arma el mail con los comprobantes a revisar. Varios, separados por coma.</small>
          </label>

          <fieldset class="mf-panel mf-panel-acento m-0 !bg-panel-alt">
            <legend class="sr-only">ARCA</legend>
            <h3 class="!mt-0">ARCA</h3>
            <div class="cl-form-arca grid gap-3 sm:grid-cols-2">
              <label class="mf-campo">
                <span>CUIT de ingreso</span>
                <input class="mf-input mono" name="cuit_login" inputmode="numeric" [value]="cuitLogin()" (input)="cuitLogin.set($any($event.target).value)"
                  (blur)="cuitLogin.set(formatear(cuitLogin()))" placeholder="20-12345678-9" [attr.aria-invalid]="cuitInvalido(cuitLogin())" />
                <small class="mf-ayuda">Con la que se entra a la clave fiscal.</small>
              </label>
              <label class="mf-campo">
                <span>Clave fiscal</span>
                <span class="relative block">
                  <input class="mf-input pr-16" name="clave" [type]="verClave() ? 'text' : 'password'" autocomplete="new-password" [value]="clave()" (input)="clave.set($any($event.target).value)"
                    [placeholder]="cliente()?.arca?.tiene_clave ? 'Guardada · escribila solo para cambiarla' : ''" />
                  <button type="button" class="mf-btn-link absolute inset-y-0 right-3 text-xs" (click)="verClave.update((v) => !v)" [attr.aria-pressed]="verClave()">
                    {{ verClave() ? 'Ocultar' : 'Ver' }}
                  </button>
                </span>
                @if (cliente()?.arca?.tiene_clave) { <small class="mf-ayuda" style="color: var(--color-ok-texto)">Hay una clave guardada.</small> }
              </label>
              <label class="mf-campo sm:col-span-2">
                <span>CUIT representado</span>
                <input class="mf-input mono" name="cuit_representado" inputmode="numeric" [value]="cuitRepres()" (input)="cuitRepres.set($any($event.target).value)"
                  (blur)="cuitRepres.set(formatear(cuitRepres()))" placeholder="30-12345678-9" [attr.aria-invalid]="cuitInvalido(cuitRepres())" />
                <small class="mf-ayuda">La empresa cuyos comprobantes se consultan (si el contador la representa con su clave).</small>
              </label>
            </div>
          </fieldset>

          @if (error(); as e) { <app-alerta nivel="error" titulo="No se pudo guardar." [mensajes]="[e]" /> }
        </div>

        <footer class="flex items-center gap-2 border-t border-borde px-5 py-4">
          @if (cliente()) {
            <button type="button" class="mf-btn mf-btn-peligro" (click)="confirmarBorrado.set(true)" [disabled]="guardando()">Borrar cliente</button>
          }
          <button type="button" class="mf-btn mf-btn-suave ml-auto" (click)="abierto.set(false)">Cancelar</button>
          <button type="submit" class="mf-btn" [disabled]="guardando()">
            {{ guardando() ? 'Guardando…' : cliente() ? 'Guardar cambios' : 'Crear cliente' }}
          </button>
        </footer>
      </form>
    </ng-template>

    <app-confirm-modal [(abierto)]="confirmarBorrado" [titulo]="'¿Borrar ' + (cliente()?.nombre ?? '') + '?'" textoConfirmar="Sí, borrar" [peligro]="true" (confirmado)="borrar()">
      Se saca de config.json con sus claves. <strong class="text-texto">No se puede deshacer</strong>; los Excel ya generados quedan en sus carpetas.
    </app-confirm-modal>
  `,
})
export class ClienteForm {
  private readonly store = inject(ClientesStore);

  readonly abierto = model(false);
  /** true: se muestra en el panel de la derecha en vez de una ventana. */
  readonly enPanel = input(false);
  /** null = alta. */
  readonly cliente = input<Cliente | null>(null);
  readonly guardado = output<string>();

  protected readonly formatear = formatearCuit;

  protected readonly nombre = signal('');
  protected readonly cuitLogin = signal('');
  protected readonly clave = signal('');
  protected readonly cuitRepres = signal('');
  protected readonly email = signal('');
  protected readonly verClave = signal(false);
  protected readonly guardando = signal(false);
  protected readonly intentado = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly confirmarBorrado = signal(false);

  private readonly dialogo = viewChild<ElementRef<HTMLDialogElement>>('dialogo');

  constructor() {
    effect(() => {
      const abierto = this.abierto();
      const cliente = this.cliente();
      const enPanel = this.enPanel();
      const d = this.dialogo()?.nativeElement;
      untracked(() => {
        if (enPanel) {
          // En el panel, elegir otro cliente de la lista carga sus datos.
          if (abierto) this.cargar(cliente);
          return;
        }
        if (!d) return;
        if (abierto && !d.open) {
          this.cargar(cliente);
          d.showModal();
        }
        if (!abierto && d.open) d.close();
      });
    });
  }

  private cargar(c: Cliente | null): void {
    this.nombre.set(c?.nombre ?? '');
    this.cuitLogin.set(formatearCuit(c?.arca.cuit_login));
    this.clave.set('');
    this.cuitRepres.set(formatearCuit(c?.arca.cuit_representado));
    this.email.set(c?.email ?? '');
    this.verClave.set(false);
    this.intentado.set(false);
    this.error.set(null);
  }

  protected emailInvalido(): boolean {
    const v = this.email().trim();
    return !!v && !v.split(',').every((m) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(m.trim()));
  }

  protected cuitInvalido(valor: string): boolean {
    return valor.replace(/\D/g, '').length > 0 && !cuitValida(valor);
  }

  protected async guardar(): Promise<void> {
    this.intentado.set(true);
    if (!this.nombre().trim()) {
      this.error.set('Poné el nombre del cliente.');
      return;
    }
    if (this.cuitInvalido(this.cuitLogin()) || this.cuitInvalido(this.cuitRepres())) {
      this.error.set('Revisá las CUIT: tienen que tener 11 dígitos y el último (verificador) tiene que coincidir.');
      return;
    }
    if (this.emailInvalido()) {
      this.error.set('El email no tiene un formato válido (ej. contacto@empresa.com).');
      return;
    }
    const datos: ClienteEntrada = {
      nombre: this.nombre().trim(),
      arca: { cuit_login: this.cuitLogin(), clave: this.clave(), cuit_representado: this.cuitRepres() },
      email: this.email().split(',').map((m) => m.trim()).filter(Boolean).join(', '),
    };
    this.guardando.set(true);
    this.error.set(null);
    try {
      const c = await this.store.guardar(this.cliente()?.nombre ?? null, datos);
      this.abierto.set(false);
      this.guardado.emit(c.nombre);
    } catch (e) {
      this.error.set(mensajeDeError(e, 'No se pudo guardar el cliente.'));
    } finally {
      this.guardando.set(false);
    }
  }

  protected async borrar(): Promise<void> {
    const c = this.cliente();
    if (!c) return;
    this.guardando.set(true);
    try {
      await this.store.borrar(c.nombre);
      this.abierto.set(false);
    } catch (e) {
      this.error.set(mensajeDeError(e, 'No se pudo borrar el cliente.'));
    } finally {
      this.guardando.set(false);
    }
  }
}
