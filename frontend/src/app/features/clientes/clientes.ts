import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { CuitPipe } from '../../core/cuit.pipe';
import { Cliente } from '../../core/models';
import { ClientesStore } from '../../core/services/clientes.store';
import { SesionService } from '../../core/services/sesion.service';
import { Alerta } from '../../core/ui/alerta';
import { ControlSegmentado, OpcionSegmentada } from '../../core/ui/control-segmentado';
import { Encabezado } from '../../core/ui/encabezado';
import { ClienteForm } from './cliente-form';

type Filtro = 'todos' | 'incompletos';

@Component({
  selector: 'app-clientes',
  imports: [RouterLink, CuitPipe, Alerta, ControlSegmentado, Encabezado, ClienteForm],
  template: `
    <app-encabezado titulo="Clientes" bajada="Las empresas que se controlan y los datos con los que los bots entran a ARCA y a Xubio.">
      <svg icono class="h-6 w-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
        <path d="M3 21h18" /><path d="M5 21V7l7-4 7 4v14" /><path d="M9 21v-6h6v6" /><path d="M9 10h.01M15 10h.01" />
      </svg>
      @if (sesion.esSupervisor()) {
        <button type="button" (click)="abrir(null)"
          class="inline-flex items-center gap-2 rounded-lg bg-brand-700 px-4 py-2 text-sm font-bold text-white shadow-md shadow-brand-700/20 hover:bg-brand-800">
          <svg class="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
          Nuevo cliente
        </button>
      }
    </app-encabezado>

    @if (store.error(); as e) { <app-alerta class="mb-6" nivel="error" [mensajes]="[e]" /> }
    @if (!sesion.esSupervisor()) {
      <app-alerta class="mb-6" nivel="info" [mensajes]="['Solo un supervisor puede dar de alta, editar o borrar clientes. Si falta un dato, pedíselo.']" />
    }
    @if (guardadoRecien(); as g) {
      <app-alerta class="mb-6" nivel="info" [mensajes]="['Se guardó ' + g + '.']" [descartable]="true" (cerrado)="guardadoRecien.set(null)" />
    }

    <section class="rounded-2xl border border-line bg-white">
      <div class="flex flex-wrap items-center gap-3 border-b border-line px-5 py-4">
        <label class="relative min-w-[16rem] flex-1">
          <span class="sr-only">Buscar cliente</span>
          <svg class="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-slate-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true">
            <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
          </svg>
          <input type="search" [value]="busqueda()" (input)="busqueda.set($any($event.target).value)" placeholder="Buscar por nombre o CUIT"
            class="w-full rounded-lg border border-line py-2 pr-3 pl-9 text-sm focus:border-brand-400 focus:ring-2 focus:ring-brand-100 focus:outline-none" />
        </label>
        <app-control-segmentado [opciones]="filtros()" [(valor)]="filtro" aria-label="Filtrar clientes" />
      </div>

      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead class="border-b border-line text-left text-xs tracking-wider text-muted uppercase">
            <tr>
              <th scope="col" class="px-5 py-3 font-semibold">Cliente</th>
              <th scope="col" class="px-5 py-3 font-semibold">CUIT representado</th>
              <th scope="col" class="px-5 py-3 font-semibold">Ingreso ARCA</th>
              <th scope="col" class="px-5 py-3 font-semibold">Xubio</th>
              <th scope="col" class="px-5 py-3 font-semibold">Estado</th>
              <th scope="col" class="px-5 py-3"><span class="sr-only">Acciones</span></th>
            </tr>
          </thead>
          <tbody class="divide-y divide-line">
            @for (c of visibles(); track c.nombre) {
              <tr class="hover:bg-canvas/60">
                <td class="max-w-[18rem] px-5 py-3"><p class="truncate font-semibold" [title]="c.nombre">{{ c.nombre }}</p></td>
                <td class="px-5 py-3 tabular-nums">{{ c.arca.cuit_representado ? (c.arca.cuit_representado | cuit) : '—' }}</td>
                <td class="px-5 py-3">
                  <span class="tabular-nums">{{ c.arca.cuit_login ? (c.arca.cuit_login | cuit) : '—' }}</span>
                  <span class="ml-2 text-xs" [class]="c.arca.tiene_clave ? 'text-emerald-700' : 'font-semibold text-amber-700'">{{ c.arca.tiene_clave ? 'con clave' : 'sin clave' }}</span>
                </td>
                <td class="max-w-[14rem] px-5 py-3">
                  @if (c.xubio.url) {
                    <span class="block truncate text-muted" [title]="c.xubio.url">{{ c.xubio.url.replace('https://', '') }}</span>
                  } @else {
                    <span class="text-xs font-semibold text-amber-700">sin dirección</span>
                  }
                </td>
                <td class="px-5 py-3">
                  @if (c.faltantes.length) {
                    <span class="inline-flex rounded-full bg-amber-50 px-2.5 py-0.5 text-xs font-semibold text-amber-800 ring-1 ring-amber-200 ring-inset" [title]="'Falta: ' + c.faltantes.join(', ')">Faltan {{ c.faltantes.length }} dato(s)</span>
                  } @else {
                    <span class="inline-flex rounded-full bg-emerald-50 px-2.5 py-0.5 text-xs font-semibold text-emerald-700 ring-1 ring-emerald-200 ring-inset">Completo</span>
                  }
                </td>
                <td class="px-5 py-3 text-right">
                  @if (sesion.esSupervisor()) {
                    <button type="button" (click)="abrir(c)" class="rounded-lg px-3 py-1.5 text-sm font-semibold text-brand-700 hover:bg-brand-50" [attr.aria-label]="'Editar ' + c.nombre">Editar</button>
                  }
                </td>
              </tr>
            } @empty {
              <tr>
                <td colspan="6" class="px-5 py-12 text-center text-muted">
                  @if (store.cargando() && !store.cargado()) {
                    Cargando clientes…
                  } @else if (busqueda() || filtro() !== 'todos') {
                    Ningún cliente coincide con el filtro.
                  } @else {
                    Todavía no hay clientes. @if (sesion.esSupervisor()) { <button type="button" (click)="abrir(null)" class="font-semibold text-brand-700 hover:underline">Cargar el primero</button> }
                  }
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
      <p class="border-t border-line px-5 py-3 text-xs text-muted">
        {{ store.lista().length }} cliente(s) · se guardan en <span class="font-mono">config.json</span>, el mismo archivo que usa la app de escritorio.
        <a routerLink="/control" class="ml-1 font-semibold text-brand-700 hover:underline">Ir a Nuevo control</a>
      </p>
    </section>

    <app-cliente-form [(abierto)]="formAbierto" [cliente]="editando()" (guardado)="guardadoRecien.set($event)" />
  `,
})
export class ClientesPantalla {
  protected readonly store = inject(ClientesStore);
  protected readonly sesion = inject(SesionService);

  protected readonly busqueda = signal('');
  protected readonly filtro = signal<Filtro>('todos');
  protected readonly formAbierto = signal(false);
  protected readonly editando = signal<Cliente | null>(null);
  protected readonly guardadoRecien = signal<string | null>(null);

  protected readonly filtros = computed<OpcionSegmentada<Filtro>[]>(() => [
    { id: 'todos', etiqueta: `Todos (${this.store.lista().length})` },
    { id: 'incompletos', etiqueta: `Con datos faltantes (${this.store.incompletos()})` },
  ]);

  protected readonly visibles = computed(() => {
    const q = this.busqueda().trim().toLocaleLowerCase('es');
    const digitos = q.replace(/\D/g, '');
    return this.store
      .lista()
      .filter((c) => this.filtro() === 'todos' || c.faltantes.length)
      .filter(
        (c) =>
          !q ||
          c.nombre.toLocaleLowerCase('es').includes(q) ||
          (digitos.length >= 3 && (c.arca.cuit_representado.includes(digitos) || c.arca.cuit_login.includes(digitos))),
      );
  });

  constructor() {
    void this.store.cargar(true);
  }

  protected abrir(c: Cliente | null): void {
    this.editando.set(c);
    this.guardadoRecien.set(null);
    this.formAbierto.set(true);
  }
}
