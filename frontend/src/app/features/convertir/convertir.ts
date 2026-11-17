import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { descargarBlob } from '../../core/descargar';
import { ResultadoConversion, mensajeDeError } from '../../core/models';
import { ApiService } from '../../core/services/api.service';
import { ClientesStore } from '../../core/services/clientes.store';
import { AlertasService } from '../../core/ui/alertas.service';
import { Alerta } from '../../core/ui/alerta';
import { Encabezado } from '../../core/ui/encabezado';
import { ToastService } from '../../core/ui/toast';

/** Lo que dice el nombre del AUX de la app: operación, período y cliente. */
const NOMBRE_AUX = /^AUX - Control de facturas (Ventas|Compras) con AFIP - (\d{2}-\d{2}) - (.+?)(?: - \d+\S* control)?\.xlsx?$/i;

interface ArchivoElegido {
  archivo: File;
  operacion: string;
  cliente: string;
}

/**
 * Convertir AUX: aparte de los controles. Se eligen los Excel de auditoría (AUX)
 * y vuelve, por cliente y período, un .zip con las partidas de ARCA no
 * encontradas en el Libro IVA (Ventas y Compras) en el CSV de Mis Comprobantes.
 * No se guarda nada en las carpetas de los controles.
 */
@Component({
  selector: 'app-convertir',
  imports: [Encabezado, Alerta, FormsModule],
  styles: `
    :host { display: flex; flex-direction: column; gap: 14px; }
    .zona { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 28px 16px; border: 2px dashed #cbd5e1; border-radius: 12px; background: #f8fafc; text-align: center; transition: border-color .15s, background .15s; }
    .zona.encima { border-color: var(--color-acento); background: #eff6ff; }
    .op { display: inline-block; min-width: 68px; padding: 0 8px; border-radius: 4px; font-size: .74rem; font-weight: 800; text-transform: uppercase; text-align: center; }
    .op-ventas { background: #e0f2fe; color: #075985; }
    .op-compras { background: #fef3c7; color: #92400e; }
    .op-otro { background: #f1f5f9; color: #475569; }
  `,
  template: `
    <app-encabezado
      titulo="Convertir AUX a CSV"
      bajada="Partidas de ARCA no encontradas en el Libro IVA, en el formato CSV de Mis Comprobantes. Aparte de los controles."
    />

    <section class="mf-panel" aria-labelledby="t-elegir">
      <h2 id="t-elegir" class="m-0 text-lg font-bold">1. Elegí los AUX</h2>
      <p class="mf-sub m-0 mt-1">
        Los Excel de auditoría (AUX) de Ventas y de Compras, de uno o varios clientes, con cualquier nombre. Se detecta
        solo la hoja de faltantes, si es Ventas o Compras, el cliente y el período.
      </p>

      <div
        class="zona mt-3"
        [class.encima]="encima()"
        (dragover)="$event.preventDefault(); encima.set(true)"
        (dragleave)="encima.set(false)"
        (drop)="soltar($event)"
      >
        <span class="font-semibold">Arrastrá los AUX acá</span>
        <span class="mf-tenue text-sm">o</span>
        <label class="mf-btn mf-btn-suave cursor-pointer">
          Elegir archivos
          <input type="file" accept=".xlsx,.xls" multiple class="sr-only" (change)="elegir($event)" />
        </label>
      </div>

      @if (elegidos().length) {
        <ul class="ej-archivos mt-3">
          @for (e of elegidos(); track e.archivo.name; let i = $index) {
            <li>
              <span class="op" [class]="'op op-' + (e.operacion === 'Ventas' ? 'ventas' : e.operacion === 'Compras' ? 'compras' : 'otro')">{{ e.operacion }}</span>
              <span class="min-w-0 flex-1">
                <strong class="block truncate" [title]="e.archivo.name">{{ e.cliente }}</strong>
                <span class="mf-tenue block truncate text-sm">{{ e.archivo.name }}</span>
              </span>
              <button type="button" class="mf-icono-btn" (click)="quitar(i)" [attr.aria-label]="'Quitar ' + e.archivo.name" [disabled]="convirtiendo()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" class="h-3.5 w-3.5" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
              </button>
            </li>
          }
        </ul>
        <div class="mt-3 flex flex-wrap items-center justify-end gap-2">
          <button type="button" class="mf-btn mf-btn-suave" (click)="limpiar()" [disabled]="convirtiendo()">Sacar todos</button>
          <button type="button" class="mf-btn inline-flex items-center gap-2" (click)="convertir()" [disabled]="convirtiendo()" [attr.aria-busy]="convirtiendo()">
            @if (convirtiendo()) {
              <span class="inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden="true"></span>
            }
            {{ convirtiendo() ? 'Convirtiendo…' : 'Convertir ' + elegidos().length + (elegidos().length === 1 ? ' archivo' : ' archivos') }}
          </button>
        </div>
      }

      @if (error(); as err) { <app-alerta class="mt-3" nivel="error" [mensajes]="[err]" [descartable]="true" (cerrado)="error.set(null)" /> }
    </section>

    @if (resultados(); as lista) {
      <section class="mf-panel" aria-labelledby="t-resultado">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <h2 id="t-resultado" class="m-0 text-lg font-bold">2. Cargalos en Xubio</h2>
            <p class="mf-sub m-0 mt-1">
              Se genera un .zip por operación (Compras y/o Ventas), con un solo CSV adentro: el formato que pide Xubio en
              «Importar desde Mis Comprobantes (ARCA)». «Cargar en Xubio» lo sube por vos; «Descargar .zip» te lo deja para
              hacerlo a mano. «Ver en Excel» trae los mismos datos para revisarlos.
            </p>
          </div>
          @if (todosLosZips().length > 1) {
            <button type="button" class="mf-btn mf-btn-suave" (click)="descargarTodos()">Descargar todos ({{ todosLosZips().length }})</button>
          }
        </div>
        @if (sinPartidas().length) {
          <app-alerta
            class="mt-3"
            nivel="info"
            titulo="Sin partidas de ARCA no encontradas"
            [mensajes]="sinPartidas()"
          />
        }
        <ul class="ej-archivos mt-3">
          @for (r of lista; track $index) {
            @let i = $index;
            <li class="!items-start">
              <span class="min-w-0 flex-1">
                <strong class="block truncate" [title]="r.cliente">{{ r.cliente }}@if (r.periodo) { · {{ r.periodo }} }</strong>
                @if (r.error) {
                  <span class="text-sm" style="color: var(--color-error)">{{ r.error }}</span>
                } @else {
                  @for (a of r.avisos; track a) {
                    <span class="block text-sm" style="color: var(--color-acento)">ⓘ {{ a }}</span>
                  }
                  @if (operacionesConZip(r).length) {
                    <span class="mt-2 flex flex-wrap items-center gap-2 text-sm">
                      <label class="mf-tenue" [attr.for]="'empresa-' + i">Empresa en Xubio</label>
                      <select class="mf-input !w-auto !py-1 text-sm" [id]="'empresa-' + i" [ngModel]="empresaDe(r, i)" (ngModelChange)="elegirEmpresa(i, $event)">
                        <option value="">Elegí el cliente…</option>
                        @for (c of clientes.lista(); track c.nombre) { <option [value]="c.nombre">{{ c.nombre }}</option> }
                      </select>
                    </span>
                    @for (op of operacionesConZip(r); track op) {
                      @let clave = i + '-' + op;
                      <span class="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-white px-2.5 py-2 text-sm">
                        <span class="min-w-0 flex-1">
                          <strong>{{ op }}</strong>
                          <span class="mf-tenue"> · {{ r.partidas[op] }} {{ r.partidas[op] === 1 ? 'partida' : 'partidas' }}</span>
                        </span>
                        @if (cargados()[clave]) {
                          <span class="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-semibold" style="color: var(--color-ok-texto); background: #e7f6ec">✓ Cargado en Xubio</span>
                        } @else {
                          <button type="button" class="mf-btn mf-btn-chico inline-flex items-center gap-2" (click)="cargarEnXubio(r, i, op)" [disabled]="!!cargandoXubio() || !empresaDe(r, i)" [attr.aria-busy]="cargandoXubio() === clave" [title]="empresaDe(r, i) ? '' : 'Elegí primero la empresa en Xubio'">
                            @if (cargandoXubio() === clave) {
                              <span class="inline-block h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden="true"></span>
                              Subiendo a Xubio…
                            } @else {
                              Cargar {{ op }} en Xubio
                            }
                          </button>
                        }
                        <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="bajar(r.zips[op]!, 'application/zip')" [attr.aria-label]="'Descargar ' + r.zips[op]!.nombre">Descargar .zip</button>
                      </span>
                      @if (estadoXubio()[clave]; as est) {
                        <span class="mt-1 block text-sm" [style.color]="est.ok ? 'var(--color-ok-texto)' : 'var(--color-error)'">{{ est.texto }}</span>
                      }
                    }
                  }
                }
              </span>
              @if (r.excel) {
                <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="bajar(r.excel, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')" [attr.aria-label]="'Ver en Excel ' + r.excel.nombre" title="Los mismos datos en Excel, con el CAE y el CUIT completos">Ver en Excel</button>
              }
            </li>
          }
        </ul>
      </section>
    }
  `,
})
export class ConvertirPantalla {
  private readonly api = inject(ApiService);
  private readonly toast = inject(ToastService);
  private readonly alertas = inject(AlertasService);
  protected readonly clientes = inject(ClientesStore);

  protected readonly elegidos = signal<ArchivoElegido[]>([]);
  protected readonly encima = signal(false);
  protected readonly convirtiendo = signal(false);
  protected readonly resultados = signal<ResultadoConversion[] | null>(null);
  protected readonly error = signal<string | null>(null);
  /** Empresa de Xubio elegida a mano por fila (si no, la que coincide con el nombre). */
  private readonly empresas = signal<Record<number, string>>({});
  /** «fila-operación» que se está subiendo, las que ya se cargaron (no se reenvían) y su mensaje. */
  protected readonly cargandoXubio = signal<string | null>(null);
  protected readonly cargados = signal<Record<string, true>>({});
  protected readonly estadoXubio = signal<Record<string, { ok: boolean; texto: string }>>({});

  constructor() {
    void this.clientes.cargar();
  }

  protected empresaDe(r: ResultadoConversion, i: number): string {
    const elegida = this.empresas()[i];
    if (elegida !== undefined) return elegida;
    const buscado = normalizar(r.cliente);
    const lista = this.clientes.lista();
    const exacta = lista.find((c) => normalizar(c.nombre) === buscado);
    const parecida = lista.find((c) => normalizar(c.nombre).includes(buscado) || buscado.includes(normalizar(c.nombre)));
    return (exacta ?? parecida)?.nombre ?? '';
  }

  protected elegirEmpresa(i: number, nombre: string): void {
    this.empresas.update((m) => ({ ...m, [i]: nombre }));
  }

  protected operacionesConZip(r: ResultadoConversion): ('Ventas' | 'Compras')[] {
    return (['Compras', 'Ventas'] as const).filter((op) => r.zips[op]);
  }

  /** Prueba: el bot carga el archivo en «Importar desde Mis Comprobantes (ARCA)» y se detiene. */
  protected async cargarEnXubio(r: ResultadoConversion, i: number, op: 'Ventas' | 'Compras'): Promise<void> {
    const empresa = this.empresaDe(r, i);
    const zip = r.zips[op];
    const clave = `${i}-${op}`;
    if (!zip || !empresa || this.cargandoXubio() || this.cargados()[clave]) return;
    const seguir = await this.alertas.confirmar(
      `Cargar ${op} en Xubio`,
      `Se abre Xubio en esta PC, en la empresa «${empresa}», y se carga el archivo en Importar desde Mis Comprobantes (ARCA).`,
      'Abrir Xubio',
      [
        `${r.partidas[op]} ${r.partidas[op] === 1 ? 'partida' : 'partidas'} de ${op}.`,
        'El bot NO aprieta «Importar»: queda la ventana abierta para que lo revises.',
        'Cerrá esa ventana de Chrome cuando termines.',
      ],
    );
    if (!seguir) return;
    this.cargandoXubio.set(clave);
    this.estadoXubio.update((m) => {
      const { [clave]: _, ...resto } = m;
      return resto;
    });
    try {
      // Solo el .zip de esta operación (un único CSV en la raíz).
      const res = await this.api.cargarEnXubio(empresa, op, zip.contenido);
      if (res.boton_importar_habilitado) {
        this.cargados.update((m) => ({ ...m, [clave]: true }));
        this.estadoXubio.update((m) => ({
          ...m,
          [clave]: { ok: true, texto: `Archivo de ${op} cargado en Xubio (${empresa}). Falta apretar «Importar» en la ventana de Chrome.` },
        }));
        this.toast.ok('Listo', `${op} cargado en Xubio (${empresa}).`);
      } else {
        this.estadoXubio.update((m) => ({
          ...m,
          [clave]: { ok: false, texto: `${op}: se abrió la importación pero Xubio no habilitó «Importar». Revisá la ventana de Chrome.` },
        }));
      }
    } catch (err) {
      this.estadoXubio.update((m) => ({ ...m, [clave]: { ok: false, texto: mensajeDeError(err, 'No se pudo abrir Xubio.') } }));
    } finally {
      this.cargandoXubio.set(null);
    }
  }

  protected readonly todosLosZips = computed(() => (this.resultados() ?? []).flatMap((r) => Object.values(r.zips).filter((z) => !!z)));
  /** El informe de arriba: clientes en los que no hubo nada que convertir. */
  protected readonly sinPartidas = computed(() =>
    (this.resultados() ?? [])
      .filter((r) => !r.error && !Object.keys(r.zips).length)
      .map((r) => `${r.cliente}${r.periodo ? ' · ' + r.periodo : ''}: todo lo de ARCA está cargado en el Libro IVA (${Object.keys(r.partidas).join(' y ')}). No se generó archivo.`),
  );

  protected elegir(ev: Event): void {
    const input = ev.target as HTMLInputElement;
    this.sumar(Array.from(input.files ?? []));
    input.value = '';
  }

  protected soltar(ev: DragEvent): void {
    ev.preventDefault();
    this.encima.set(false);
    this.sumar(Array.from(ev.dataTransfer?.files ?? []));
  }

  private sumar(archivos: File[]): void {
    const excel = archivos.filter((a) => /\.xlsx?$/i.test(a.name));
    if (excel.length < archivos.length) this.error.set('Solo se aceptan Excel (.xlsx).');
    const ya = new Set(this.elegidos().map((e) => e.archivo.name));
    const nuevos = excel.filter((a) => !ya.has(a.name)).map((archivo) => {
      const m = NOMBRE_AUX.exec(archivo.name);
      const base = archivo.name.replace(/\.xlsx?$/i, '');
      return { archivo, operacion: m ? capitalizar(m[1]) : operacionPorNombre(base), cliente: m ? m[3] : base };
    });
    this.elegidos.update((l) => [...l, ...nuevos]);
    this.resultados.set(null);
    this.empresas.set({});
    this.estadoXubio.set({});
    this.cargados.set({});
  }

  protected quitar(i: number): void {
    this.elegidos.update((l) => l.filter((_, j) => j !== i));
  }

  protected limpiar(): void {
    this.elegidos.set([]);
    this.resultados.set(null);
    this.error.set(null);
  }

  protected async convertir(): Promise<void> {
    if (this.convirtiendo() || !this.elegidos().length) return;
    this.convirtiendo.set(true);
    this.error.set(null);
    try {
      const archivos = await Promise.all(this.elegidos().map(async (e) => ({ nombre: e.archivo.name, contenido: await base64De(e.archivo) })));
      const r = await this.api.convertirAux(archivos);
      this.resultados.set(r.resultados);
      this.cargados.set({});
      this.estadoXubio.set({});
      const ok = r.resultados.reduce((n, x) => n + Object.keys(x.zips).length, 0);
      if (ok) this.toast.ok('Listo', `${ok} ${ok === 1 ? 'archivo .zip listo' : 'archivos .zip listos'} para Xubio.`);
      const vacios = r.resultados.filter((x) => !x.error && !Object.keys(x.zips).length).length;
      if (vacios) {
        this.toast.info(
          'Sin partidas de ARCA no encontradas',
          vacios === 1 ? 'Un cliente no tiene nada para convertir: todo ARCA está en su Libro IVA.' : `${vacios} clientes no tienen nada para convertir: todo ARCA está en su Libro IVA.`,
        );
      }
    } catch (err) {
      this.error.set(mensajeDeError(err, 'No se pudieron convertir los AUX.'));
    } finally {
      this.convirtiendo.set(false);
    }
  }

  protected bajar(archivo: { nombre: string; contenido: string }, tipo: string): void {
    const bytes = Uint8Array.from(atob(archivo.contenido), (c) => c.charCodeAt(0));
    descargarBlob(archivo.nombre, new Blob([bytes], { type: tipo }));
  }

  protected descargarTodos(): void {
    // Uno tras otro: algunos navegadores bloquean varias descargas simultáneas.
    this.todosLosZips().forEach((z, i) => setTimeout(() => this.bajar(z, 'application/zip'), i * 300));
  }
}

/** Solo para la etiqueta: el servidor detecta la operación por el contenido del Excel. */
function operacionPorNombre(nombre: string): string {
  const n = nombre.toLowerCase();
  const ventas = /venta|emitid/.test(n);
  const compras = /compra|recibid/.test(n);
  return ventas && !compras ? 'Ventas' : compras && !ventas ? 'Compras' : 'AUX';
}

function normalizar(s: string): string {
  return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function capitalizar(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

/** El archivo en base64 (sin el prefijo «data:…;base64,»). */
function base64De(archivo: File): Promise<string> {
  return new Promise((ok, mal) => {
    const lector = new FileReader();
    lector.onload = () => ok(String(lector.result).split(',', 2)[1] ?? '');
    lector.onerror = () => mal(new Error(`No se pudo leer «${archivo.name}».`));
    lector.readAsDataURL(archivo);
  });
}
