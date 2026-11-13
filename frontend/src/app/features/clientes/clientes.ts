import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { CuitPipe } from '../../core/cuit.pipe';
import { Cliente } from '../../core/models';
import { ClientesStore } from '../../core/services/clientes.store';
import { SesionService } from '../../core/services/sesion.service';
import { Alerta } from '../../core/ui/alerta';
import { Encabezado } from '../../core/ui/encabezado';
import { ClienteForm } from './cliente-form';

@Component({
  selector: 'app-clientes',
  imports: [RouterLink, CuitPipe, Alerta, Encabezado, ClienteForm],
  template: `
    <app-encabezado titulo="Clientes" bajada="Las empresas que se controlan y los datos con los que los bots entran a ARCA y a Xubio.">
      @if (sesion.esSupervisor()) {
        <button type="button" class="mf-btn" (click)="abrir(null)">+ Nuevo cliente</button>
      }
    </app-encabezado>

    @if (store.error(); as e) { <app-alerta nivel="error" titulo="No se pudieron cargar los clientes." [mensajes]="[e]" /> }
    @if (!sesion.esSupervisor()) {
      <app-alerta nivel="info" [mensajes]="['Solo un supervisor puede dar de alta, editar o borrar clientes. Si falta un dato, pedíselo.']" />
    }
    @if (guardadoRecien(); as g) {
      <app-alerta nivel="ok" [mensajes]="['Se guardó ' + g + '.']" [descartable]="true" (cerrado)="guardadoRecien.set(null)" />
    }

    <!-- Tarjetas: siguen al total, no a los filtros -->
    <section class="mf-cards" aria-label="Resumen">
      <article class="mf-card">
        <span class="mf-card-num">{{ store.lista().length }}</span>
        <span class="mf-card-etiq">Clientes</span>
      </article>
      <article class="mf-card">
        <span class="mf-card-num">{{ store.lista().length - store.incompletos() }}</span>
        <span class="mf-card-etiq">Con datos completos</span>
      </article>
      <article class="mf-card" [class.mf-card-alerta]="store.incompletos() > 0">
        <span class="mf-card-num">{{ store.incompletos() }}</span>
        <span class="mf-card-etiq">Con datos faltantes</span>
      </article>
      <article class="mf-card">
        <span class="mf-card-num">{{ sinClave() }}</span>
        <span class="mf-card-etiq">Sin clave fiscal</span>
      </article>
    </section>

    <section class="mf-panel" aria-labelledby="t-lista">
      <div class="mf-panel-titulo">
        <h2 id="t-lista">Listado</h2>
        <span class="mf-sub">{{ visibles().length }} de {{ store.lista().length }}</span>
        <div class="mf-filtros">
          <input class="mf-input !w-64" type="search" [value]="busqueda()" (input)="busqueda.set($any($event.target).value)" placeholder="Buscar nombre o CUIT…" aria-label="Buscar cliente" />
          <label class="mf-check">
            <input type="checkbox" [checked]="soloIncompletos()" (change)="soloIncompletos.set($any($event.target).checked)" />
            Con datos faltantes
          </label>
        </div>
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
              <tr [class.mf-fila]="sesion.esSupervisor()" (click)="sesion.esSupervisor() && abrir(c)">
                <td class="max-w-[18rem] truncate font-semibold" [title]="c.nombre">{{ c.nombre }}</td>
                <td class="mono">{{ c.arca.cuit_representado ? (c.arca.cuit_representado | cuit) : '—' }}</td>
                <td class="mono">{{ c.arca.cuit_login ? (c.arca.cuit_login | cuit) : '—' }}</td>
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
                  @if (sesion.esSupervisor()) {
                    <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="$event.stopPropagation(); abrir(c)" [attr.aria-label]="'Editar ' + c.nombre">Editar</button>
                  }
                </td>
              </tr>
            } @empty {
              <tr>
                <td colspan="7" class="mf-tenue">
                  @if (store.cargando() && !store.cargado()) {
                    Cargando clientes…
                  } @else if (busqueda() || soloIncompletos()) {
                    Nada que mostrar con esos filtros.
                  } @else {
                    Todavía no hay clientes.
                    @if (sesion.esSupervisor()) { <button type="button" class="mf-btn-link" (click)="abrir(null)">Cargar el primero</button> }
                  }
                </td>
              </tr>
            }
          </tbody>
        </table>
      </div>
      <p class="mf-ayuda mb-0 mt-3">
        Se guardan en <code>config.json</code>, el mismo archivo que usa la app de escritorio.
        <a routerLink="/control" class="mf-btn-link">Ir a Nuevo control</a>
      </p>
    </section>

    <app-cliente-form [(abierto)]="formAbierto" [cliente]="editando()" (guardado)="guardadoRecien.set($event)" />
  `,
})
export class ClientesPantalla {
  protected readonly store = inject(ClientesStore);
  protected readonly sesion = inject(SesionService);

  protected readonly busqueda = signal('');
  protected readonly soloIncompletos = signal(false);
  protected readonly formAbierto = signal(false);
  protected readonly editando = signal<Cliente | null>(null);
  protected readonly guardadoRecien = signal<string | null>(null);

  protected readonly sinClave = computed(() => this.store.lista().filter((c) => !c.arca.tiene_clave).length);

  protected readonly visibles = computed(() => {
    const q = this.busqueda().trim().toLocaleLowerCase('es');
    const digitos = q.replace(/\D/g, '');
    return this.store
      .lista()
      .filter((c) => !this.soloIncompletos() || c.faltantes.length)
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
