import { Component, ElementRef, effect, inject, input, model, output, signal, viewChild } from '@angular/core';
import { formatearCuit } from '../../core/cuit.pipe';
import { Cliente, ClienteEntrada, mensajeDeError } from '../../core/models';
import { ClientesStore } from '../../core/services/clientes.store';
import { Alerta } from '../../core/ui/alerta';
import { ConfirmModal } from '../../core/ui/confirm-modal';

const CAMPO =
  'w-full rounded-lg border border-line px-3 py-2 text-sm focus:border-brand-400 focus:ring-2 focus:ring-brand-100 focus:outline-none aria-[invalid=true]:border-red-300';

/**
 * Alta y edición de un cliente en un <dialog>. La clave fiscal no viaja al
 * navegador: al editar, dejarla vacía mantiene la que ya estaba guardada.
 */
@Component({
  selector: 'app-cliente-form',
  imports: [Alerta, ConfirmModal],
  template: `
    <dialog #dialogo (close)="abierto.set(false)" aria-labelledby="t-cliente-form"
      class="m-auto w-[min(40rem,calc(100vw-2rem))] rounded-2xl border border-line bg-white p-0 text-ink shadow-xl backdrop:bg-slate-900/40 backdrop:backdrop-blur-sm">
      <form (submit)="$event.preventDefault(); guardar()" novalidate>
        <header class="flex items-start justify-between gap-3 border-b border-line px-6 py-4">
          <div>
            <h2 id="t-cliente-form" class="text-lg font-semibold">{{ cliente() ? 'Editar cliente' : 'Nuevo cliente' }}</h2>
            <p class="text-sm text-muted">Los datos con los que los bots entran a ARCA y a Xubio.</p>
          </div>
          <button type="button" (click)="abierto.set(false)" class="-mr-2 rounded-lg p-2 text-muted hover:bg-canvas hover:text-ink" aria-label="Cerrar">
            <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
          </button>
        </header>

        <div class="max-h-[70vh] space-y-6 overflow-y-auto px-6 py-5">
          <label class="block">
            <span class="mb-1 block text-sm font-medium">Nombre del cliente</span>
            <input name="nombre" [value]="nombre()" (input)="nombre.set($any($event.target).value)" [class]="campo" required
              [attr.aria-invalid]="intentado() && !nombre().trim()" placeholder="Como figura en Xubio (se usa para elegir la empresa)" />
            <span class="mt-1 block text-xs text-muted">Se usa también como nombre de la carpeta del cliente.</span>
          </label>

          <fieldset class="rounded-xl border border-line p-4">
            <legend class="px-1 text-sm font-semibold text-brand-700">ARCA</legend>
            <div class="grid gap-3 sm:grid-cols-2">
              <label class="block">
                <span class="mb-1 block text-sm font-medium">CUIT de ingreso</span>
                <input name="cuit_login" inputmode="numeric" [value]="cuitLogin()" (input)="cuitLogin.set($any($event.target).value)" (blur)="cuitLogin.set(formatear(cuitLogin()))"
                  [class]="campo" placeholder="20-12345678-9" [attr.aria-invalid]="cuitInvalido(cuitLogin())" />
                <span class="mt-1 block text-xs text-muted">Con la que se entra a la clave fiscal.</span>
              </label>
              <label class="block">
                <span class="mb-1 block text-sm font-medium">Clave fiscal</span>
                <span class="relative block">
                  <input name="clave" [type]="verClave() ? 'text' : 'password'" autocomplete="new-password" [value]="clave()" (input)="clave.set($any($event.target).value)"
                    [class]="campo + ' pr-10'" [placeholder]="cliente()?.arca?.tiene_clave ? 'Guardada · escribila solo para cambiarla' : ''" />
                  <button type="button" (click)="verClave.update((v) => !v)" [attr.aria-label]="verClave() ? 'Ocultar clave' : 'Mostrar clave'" [attr.aria-pressed]="verClave()"
                    class="absolute inset-y-0 right-0 flex items-center px-3 text-slate-400 hover:text-ink">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="h-4 w-4" aria-hidden="true">
                      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z" /><circle cx="12" cy="12" r="3" />
                    </svg>
                  </button>
                </span>
                @if (cliente()?.arca?.tiene_clave) {
                  <span class="mt-1 flex items-center gap-1 text-xs text-emerald-700">
                    <svg class="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 6 9 17l-5-5" /></svg>
                    Hay una clave guardada
                  </span>
                }
              </label>
              <label class="block sm:col-span-2">
                <span class="mb-1 block text-sm font-medium">CUIT representado</span>
                <input name="cuit_representado" inputmode="numeric" [value]="cuitRepres()" (input)="cuitRepres.set($any($event.target).value)" (blur)="cuitRepres.set(formatear(cuitRepres()))"
                  [class]="campo" placeholder="30-12345678-9" [attr.aria-invalid]="cuitInvalido(cuitRepres())" />
                <span class="mt-1 block text-xs text-muted">La empresa cuyos comprobantes se consultan (si el contador la representa con su clave).</span>
              </label>
            </div>
          </fieldset>

          <fieldset class="rounded-xl border border-line p-4">
            <legend class="px-1 text-sm font-semibold text-brand-700">Xubio</legend>
            <label class="block">
              <span class="mb-1 block text-sm font-medium">Dirección de la empresa en Xubio</span>
              <input name="url" type="url" [value]="url()" (input)="url.set($any($event.target).value)" [class]="campo" placeholder="https://xubio.com/NXV/home"
                spellcheck="false" [attr.aria-invalid]="!!url().trim() && !url().trim().startsWith('http')" />
              <span class="mt-1 block text-xs text-muted">La que aparece en la barra del navegador con la empresa abierta. La cuenta de Xubio se elige al ejecutar.</span>
            </label>
          </fieldset>

          @if (error(); as e) { <app-alerta nivel="error" [mensajes]="[e]" /> }
        </div>

        <footer class="flex items-center gap-2 border-t border-line px-6 py-4">
          @if (cliente()) {
            <button type="button" (click)="confirmarBorrado.set(true)" [disabled]="guardando()" class="rounded-lg px-3 py-2 text-sm font-semibold text-red-700 hover:bg-red-50 disabled:opacity-50">Borrar cliente</button>
          }
          <button type="button" (click)="abierto.set(false)" class="ml-auto rounded-lg border border-line px-4 py-2 text-sm font-semibold hover:bg-canvas">Cancelar</button>
          <button type="submit" [disabled]="guardando()" class="rounded-lg bg-brand-700 px-5 py-2 text-sm font-bold text-white hover:bg-brand-800 disabled:bg-brand-400">
            {{ guardando() ? 'Guardando…' : cliente() ? 'Guardar cambios' : 'Crear cliente' }}
          </button>
        </footer>
      </form>
    </dialog>

    <app-confirm-modal [(abierto)]="confirmarBorrado" [titulo]="'¿Borrar ' + (cliente()?.nombre ?? '') + '?'" textoConfirmar="Sí, borrar" [peligro]="true" (confirmado)="borrar()">
      Se saca de config.json con sus claves. <strong class="text-ink">No se puede deshacer</strong>; los Excel ya generados quedan en sus carpetas.
    </app-confirm-modal>
  `,
})
export class ClienteForm {
  private readonly store = inject(ClientesStore);

  readonly abierto = model(false);
  /** null = alta. */
  readonly cliente = input<Cliente | null>(null);
  readonly guardado = output<string>();

  protected readonly campo = CAMPO;
  protected readonly formatear = formatearCuit;

  protected readonly nombre = signal('');
  protected readonly cuitLogin = signal('');
  protected readonly clave = signal('');
  protected readonly cuitRepres = signal('');
  protected readonly url = signal('');
  protected readonly verClave = signal(false);
  protected readonly guardando = signal(false);
  protected readonly intentado = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly confirmarBorrado = signal(false);

  private readonly dialogo = viewChild.required<ElementRef<HTMLDialogElement>>('dialogo');

  constructor() {
    effect(() => {
      const d = this.dialogo().nativeElement;
      if (this.abierto() && !d.open) {
        this.cargar(this.cliente());
        d.showModal();
      }
      if (!this.abierto() && d.open) d.close();
    });
  }

  private cargar(c: Cliente | null): void {
    this.nombre.set(c?.nombre ?? '');
    this.cuitLogin.set(formatearCuit(c?.arca.cuit_login));
    this.clave.set('');
    this.cuitRepres.set(formatearCuit(c?.arca.cuit_representado));
    this.url.set(c?.xubio.url ?? '');
    this.verClave.set(false);
    this.intentado.set(false);
    this.error.set(null);
  }

  protected cuitInvalido(valor: string): boolean {
    const d = valor.replace(/\D/g, '');
    return d.length > 0 && d.length !== 11;
  }

  protected async guardar(): Promise<void> {
    this.intentado.set(true);
    if (!this.nombre().trim()) {
      this.error.set('Poné el nombre del cliente.');
      return;
    }
    if (this.cuitInvalido(this.cuitLogin()) || this.cuitInvalido(this.cuitRepres())) {
      this.error.set('Las CUIT tienen que tener 11 dígitos.');
      return;
    }
    const datos: ClienteEntrada = {
      nombre: this.nombre().trim(),
      arca: { cuit_login: this.cuitLogin(), clave: this.clave(), cuit_representado: this.cuitRepres() },
      xubio: { url: this.url().trim() },
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
