import { Component, DestroyRef, computed, effect, inject, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';
import { CuitPipe, formatearCuit } from '../../core/cuit.pipe';
import { Cliente, mensajeDeError } from '../../core/models';
import { AccesoClientesService } from '../../core/services/acceso-clientes.service';
import { ClientesStore } from '../../core/services/clientes.store';
import { PerspectivaService } from '../../core/services/perspectiva.service';
import { Alerta } from '../../core/ui/alerta';
import { Encabezado } from '../../core/ui/encabezado';
import { RecorridoService } from '../../core/ui/recorrido.service';
import { ClienteForm } from './cliente-form';

@Component({
  selector: 'app-clientes',
  imports: [RouterLink, CuitPipe, Alerta, Encabezado, ClienteForm],
  template: `
    <app-encabezado titulo="Clientes" bajada="Las empresas que se controlan y los datos con los que los bots entran a ARCA y a Xubio.">
      @if (acceso.desbloqueado()) {
        @if (acceso.configurada()) {
          <button type="button" class="mf-btn mf-btn-suave" (click)="bloquear()" title="Vuelve a pedir la contraseña">Bloquear</button>
        }
        <button type="button" class="mf-btn mf-btn-suave" (click)="precargar()" [disabled]="precargando() || !perspectiva.editaClientes()" [attr.title]="bloqueoClientes()">
          {{ precargando() ? 'Precargando…' : 'Precargar clientes del estudio' }}
        </button>
      }
    </app-encabezado>

    @if (acceso.configurada() === null) {
      <p class="mf-tenue">Cargando…</p>
    } @else if (!acceso.desbloqueado()) {
      <!-- Candado liviano: solo esta sección, «Nuevo control» sigue abierto -->
      <section class="mf-panel mx-auto mt-6 max-w-sm" aria-labelledby="t-candado">
        <form class="flex flex-col gap-3" (submit)="$event.preventDefault(); entrar()">
          <h2 id="t-candado" class="m-0 text-base">
            {{ acceso.configurada() ? 'Clientes protegidos' : 'Elegí una contraseña para Clientes' }}
          </h2>
          <p class="mf-ayuda m-0">
            @if (acceso.configurada()) {
              Escribí la contraseña para ver y editar los clientes.
            } @else {
              Se va a pedir cada vez que se abra esta sección. Mínimo 8 caracteres.
            }
          </p>
          <input
            class="mf-input"
            type="password"
            [autocomplete]="acceso.configurada() ? 'current-password' : 'new-password'"
            [value]="clave()"
            (input)="clave.set($any($event.target).value)"
            aria-label="Contraseña de Clientes"
            placeholder="Contraseña"
            autofocus
          />
          @if (errorClave(); as e) { <p class="mf-ayuda m-0 text-red-600" role="alert">{{ e }}</p> }
          <button type="submit" class="mf-btn" [disabled]="!clave() || entrando()">
            {{ entrando() ? 'Entrando…' : acceso.configurada() ? 'Entrar' : 'Guardar y entrar' }}
          </button>
        </form>
      </section>
    } @else {

    @if (store.error(); as e) { <app-alerta nivel="error" titulo="No se pudieron cargar los clientes." [mensajes]="[e]" /> }
    @if (!perspectiva.editaClientes()) {
      <app-alerta nivel="info" [mensajes]="[perspectiva.canEdit() ? 'Solo un supervisor puede dar de alta, editar o borrar clientes. Si falta un dato, pedíselo.' : 'Vista de Empleado: solo lectura.']" />
    }
    @if (guardadoRecien(); as g) {
      <app-alerta nivel="ok" [mensajes]="['Se guardó ' + g + '.']" [descartable]="true" (cerrado)="guardadoRecien.set(null)" />
    }
    @if (avisoPrecarga(); as a) {
      <app-alerta [nivel]="a.nivel" [mensajes]="a.mensajes" [descartable]="true" (cerrado)="avisoPrecarga.set(null)" />
    }

    <!-- Lista a la izquierda; a la derecha el resumen o el cliente que se está editando -->
    <div class="cl-grilla" [class.cl-grilla-con-panel]="anchoGrande()">
      <section class="mf-panel" aria-labelledby="t-lista" data-tour="lista-clientes">
        <div class="mf-panel-titulo">
          <h2 id="t-lista">Listado</h2>
          <span class="mf-sub">{{ visibles().length }} de {{ store.lista().length }}</span>
          <div class="mf-filtros">
            <input class="mf-input !w-64" type="search" [value]="busqueda()" (input)="busqueda.set($any($event.target).value)" placeholder="Buscar nombre o CUIT…" aria-label="Buscar cliente" />
          </div>
        </div>

        <!-- Filtros de un clic, con su cantidad (reemplazan a las tarjetas) -->
        <div class="cl-filtros" role="group" aria-label="Filtrar clientes">
          @for (f of filtros(); track f.id) {
            <button type="button" class="cl-filtro" [class.activo]="filtro() === f.id" [class.cl-filtro-alerta]="f.alerta && f.cantidad > 0"
              [attr.aria-pressed]="filtro() === f.id" (click)="filtro.set(f.id)">
              {{ f.texto }} <span class="cl-filtro-num">{{ f.cantidad }}</span>
            </button>
          }
        </div>

        <div class="mf-scroll">
          <table class="mf-tabla">
            <thead>
              <tr>
                <th>Cliente</th>
                <th>CUIT representado</th>
                <th>CUIT de ingreso</th>
                <th>Clave fiscal</th>
                <th>Estado</th>
                <th><span class="sr-only">Acciones</span></th>
              </tr>
            </thead>
            <tbody>
              @for (c of visibles(); track c.nombre) {
                <tr [class.mf-fila]="perspectiva.editaClientes()" [class.mf-fila-activa]="formAbierto() && editando()?.nombre === c.nombre" (click)="perspectiva.editaClientes() && abrir(c)">
                  <td class="max-w-[18rem] truncate font-semibold" [title]="c.nombre">{{ c.nombre }}</td>
                  <td class="mono whitespace-nowrap">{{ c.arca.cuit_representado ? (c.arca.cuit_representado | cuit) : '—' }}</td>
                  <td class="mono whitespace-nowrap">{{ c.arca.cuit_login ? (c.arca.cuit_login | cuit) : '—' }}</td>
                  <td>
                    @if (c.arca.tiene_clave) { <span class="mf-badge mf-badge-ok">Cargada</span> } @else { <span class="mf-badge mf-badge-aviso">Falta</span> }
                  </td>
                  <td>
                    @if (c.faltantes.length) {
                      <span class="mf-badge mf-badge-error" [title]="'Falta: ' + c.faltantes.join(', ')">Faltan {{ c.faltantes.length }}</span>
                    } @else {
                      <span class="mf-badge mf-badge-acento">Completo</span>
                    }
                  </td>
                  <td class="num">
                    <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="$event.stopPropagation(); abrir(c)" [disabled]="!perspectiva.editaClientes()" [attr.title]="bloqueoClientes()" [attr.aria-label]="'Editar ' + c.nombre">Editar</button>
                  </td>
                </tr>
              } @empty {
                <tr>
                  <td colspan="7" class="mf-tenue">
                    @if (store.cargando() && !store.cargado()) {
                      Cargando clientes…
                    } @else if (busqueda() || filtro() !== 'todos') {
                      Nada que mostrar con esos filtros.
                    } @else {
                      <div class="cf-vacio !my-2">
                        <strong>Todavía no hay clientes</strong>
                        <p>Cada cliente es una empresa que vas a controlar. Necesitás su nombre como figura en Xubio, la CUIT con la que entrás a ARCA, la clave fiscal y la CUIT de la empresa.</p>
                        <button type="button" class="mf-btn" (click)="abrir(null)" [disabled]="!perspectiva.editaClientes()" [attr.title]="bloqueoClientes()">+ Cargar el primero</button>
                      </div>
                    }
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
        <p class="mf-ayuda mb-0 mt-3">
          Se guardan en <code>config.json</code>, el mismo archivo que usa la app de escritorio.
          @if (perspectiva.canEdit()) { <a routerLink="/control" class="mf-btn-link">Ir a Nuevo control</a> }
        </p>
      </section>

      @if (anchoGrande()) {
        <aside class="cl-lado" aria-label="Detalle del cliente">
          @if (formAbierto()) {
            <app-cliente-form [enPanel]="true" [(abierto)]="formAbierto" [cliente]="editando()" (guardado)="guardadoRecien.set($event)" />
          } @else {
            <section class="mf-panel cl-resumen" aria-labelledby="t-resumen-cl">
              <h2 id="t-resumen-cl">Resumen</h2>
              <dl>
                <div><dt>Clientes</dt><dd>{{ store.lista().length }}</dd></div>
                <div><dt>Con datos completos</dt><dd class="cl-ok">{{ store.lista().length - store.incompletos() }}</dd></div>
                <div><dt>Con datos faltantes</dt><dd [class.cl-mal]="store.incompletos() > 0">{{ store.incompletos() }}</dd></div>
                <div><dt>Sin clave fiscal</dt><dd [class.cl-mal]="sinClave() > 0">{{ sinClave() }}</dd></div>
              </dl>
              <p class="mf-tenue m-0 text-sm">Tocá un cliente de la lista para ver y editar sus datos acá.</p>
              <button type="button" class="mf-btn w-full" (click)="abrir(null)" data-tour="nuevo-cliente" [disabled]="!perspectiva.editaClientes()" [attr.title]="bloqueoClientes()">+ Nuevo cliente</button>
            </section>
          }
        </aside>
      }
    </div>

    }

    @if (!anchoGrande()) {
      <app-cliente-form [(abierto)]="formAbierto" [cliente]="editando()" (guardado)="guardadoRecien.set($event)" />
    }
  `,
})
export class ClientesPantalla {
  protected readonly store = inject(ClientesStore);
  protected readonly acceso = inject(AccesoClientesService);
  protected readonly perspectiva = inject(PerspectivaService);
  /** Por qué están bloqueados los botones de edición (title). */
  protected readonly bloqueoClientes = computed(() =>
    this.perspectiva.editaClientes() ? null : this.perspectiva.canEdit() ? 'Solo un supervisor puede editar clientes' : 'Bloqueado en la vista Empleado',
  );

  protected readonly busqueda = signal('');
  /** Qué clientes se muestran (los botones de arriba de la tabla). */
  protected readonly filtro = signal<'todos' | 'completos' | 'faltantes' | 'sin_clave'>('todos');
  /** Pantalla ancha: el cliente se edita en el panel de la derecha, no en una ventana. */
  protected readonly anchoGrande = signal(typeof matchMedia !== 'undefined' && matchMedia('(min-width: 1024px)').matches);
  protected readonly formAbierto = signal(false);
  protected readonly editando = signal<Cliente | null>(null);
  protected readonly guardadoRecien = signal<string | null>(null);

  protected readonly clave = signal('');
  protected readonly entrando = signal(false);
  protected readonly errorClave = signal<string | null>(null);
  protected readonly precargando = signal(false);
  protected readonly avisoPrecarga = signal<{ nivel: 'ok' | 'info' | 'error'; mensajes: string[] } | null>(null);

  protected readonly sinClave = computed(() => this.store.lista().filter((c) => !c.arca.tiene_clave).length);

  protected readonly visibles = computed(() => {
    const q = this.busqueda().trim().toLocaleLowerCase('es');
    const digitos = q.replace(/\D/g, '');
    return this.store
      .lista()
      .filter((c) => {
        const f = this.filtro();
        if (f === 'completos') return !c.faltantes.length;
        if (f === 'faltantes') return c.faltantes.length > 0;
        if (f === 'sin_clave') return !c.arca.tiene_clave;
        return true;
      })
      .filter(
        (c) =>
          !q ||
          c.nombre.toLocaleLowerCase('es').includes(q) ||
          (digitos.length >= 3 && (c.arca.cuit_representado.includes(digitos) || c.arca.cuit_login.includes(digitos))),
      );
  });

  protected readonly filtros = computed(() => {
    const total = this.store.lista().length;
    const faltan = this.store.incompletos();
    return [
      { id: 'todos' as const, texto: 'Todos', cantidad: total, alerta: false },
      { id: 'completos' as const, texto: 'Completos', cantidad: total - faltan, alerta: false },
      { id: 'faltantes' as const, texto: 'Con datos faltantes', cantidad: faltan, alerta: true },
      { id: 'sin_clave' as const, texto: 'Sin clave fiscal', cantidad: this.sinClave(), alerta: true },
    ];
  });

  constructor() {
    if (typeof matchMedia !== 'undefined') {
      const consulta = matchMedia('(min-width: 1024px)');
      const cambiar = (e: MediaQueryListEvent) => this.anchoGrande.set(e.matches);
      consulta.addEventListener('change', cambiar);
      inject(DestroyRef).onDestroy(() => consulta.removeEventListener('change', cambiar));
    }
    void this.acceso.revisar();
    void this.store.cargar(true);
    // Con la sección bloqueada no hay nada que mostrar: el recorrido arranca al desbloquearla.
    const recorrido = inject(RecorridoService);
    effect(() => {
      if (this.acceso.desbloqueado()) untracked(() => recorrido.alEntrar('/clientes'));
    });
  }

  protected async entrar(): Promise<void> {
    this.entrando.set(true);
    this.errorClave.set(null);
    try {
      await this.acceso.entrar(this.clave());
      this.clave.set('');
      void this.store.cargar(true);
    } catch (e) {
      this.errorClave.set(mensajeDeError(e, 'No se pudo verificar la contraseña.'));
    } finally {
      this.entrando.set(false);
    }
  }

  protected async bloquear(): Promise<void> {
    this.formAbierto.set(false);
    this.avisoPrecarga.set(null);
    this.guardadoRecien.set(null);
    await this.acceso.bloquear();
  }

  protected async precargar(): Promise<void> {
    if (!this.perspectiva.editaClientes()) return;
    this.precargando.set(true);
    this.avisoPrecarga.set(null);
    try {
      const r = await this.store.precargar();
      const lista = (nombres: string[]) => nombres.join(', ').replace(/\.$/, '');
      const mensajes: string[] = [];
      if (r.agregados.length) mensajes.push(`Se agregaron ${r.agregados.length}: ${lista(r.agregados)}.`);
      if (r.completados.length) mensajes.push(`Se completó la clave fiscal de ${r.completados.length}: ${lista(r.completados)}.`);
      if (r.ya_estaban.length) mensajes.push(`Ya estaban ${r.ya_estaban.length}.`);
      if (!r.con_clave) {
        mensajes.push(`Falta la clave fiscal del CUIT de ingreso ${formatearCuit(r.cuit_ingreso)}: cargala editando uno de ellos y volvé a precargar para que se copie al resto.`);
      }
      const hubo = r.agregados.length || r.completados.length;
      this.avisoPrecarga.set({ nivel: hubo ? 'ok' : 'info', mensajes: mensajes.length ? mensajes : ['No había nada para agregar.'] });
    } catch (e) {
      this.avisoPrecarga.set({ nivel: 'error', mensajes: [mensajeDeError(e, 'No se pudieron precargar los clientes.')] });
    } finally {
      this.precargando.set(false);
    }
  }

  protected abrir(c: Cliente | null): void {
    if (!this.perspectiva.editaClientes()) return;
    this.editando.set(c);
    this.guardadoRecien.set(null);
    this.formAbierto.set(true);
  }
}
