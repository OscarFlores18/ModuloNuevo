import { Component, HostListener, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { formatearCuit } from '../../core/cuit.pipe';
import { descargarBlob } from '../../core/descargar';
import { ResultadoConversion, mensajeDeError } from '../../core/models';
import { ApiService } from '../../core/services/api.service';
import { ClientesStore } from '../../core/services/clientes.store';
import { AlertasService } from '../../core/ui/alertas.service';
import { Alerta } from '../../core/ui/alerta';
import { Ayuda } from '../../core/ui/ayuda';
import { Encabezado } from '../../core/ui/encabezado';
import { ToastService } from '../../core/ui/toast';

/** Lo que dice el nombre del AUX de la app: operación, período y cliente. */
const NOMBRE_AUX = /^AUX - Control de facturas (Ventas|Compras) con AFIP - (\d{2}-\d{2}) - (.+?)(?: - \d+\S* control)?\.xlsx?$/i;

/** Lo que se cargó en Xubio desde acá: «pendiente» hasta que se aprieta «Importar». */
interface RegistroImportacion {
  estado: 'pendiente' | 'importado';
  /** ISO: cuándo se cargó o se marcó importado. */
  fecha: string;
  partidas: number;
  control: number | null;
}

const CLAVE_REGISTRO = 'cf-faltantes-xubio';

/** Queda en este navegador. Si no hay acceso (modo privado), arranca vacío. */
function leerRegistro(): Record<string, RegistroImportacion> {
  try {
    const crudo = localStorage.getItem(CLAVE_REGISTRO);
    const datos = crudo ? JSON.parse(crudo) : {};
    return datos && typeof datos === 'object' ? datos : {};
  } catch {
    return {};
  }
}

function escribirRegistro(datos: Record<string, RegistroImportacion>): void {
  try {
    localStorage.setItem(CLAVE_REGISTRO, JSON.stringify(datos));
  } catch {
    /* sin almacenamiento: se pierde al recargar */
  }
}

const ORDINALES: Record<number, string> = { 1: '1er', 2: '2do', 3: '3er', 4: '4to', 5: '5to', 6: '6to', 7: '7mo', 8: '8vo', 9: '9no', 10: '10mo' };

function ordinal(n: number): string {
  return ORDINALES[n] ?? `${n}°`;
}

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
  imports: [Encabezado, Alerta, FormsModule, RouterLink, Ayuda],
  styles: `
    :host { display: flex; flex-direction: column; gap: 14px; }
    .zona { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 28px 16px; border: 2px dashed #cbd5e1; border-radius: 12px; background: #f8fafc; text-align: center; transition: border-color .15s, background .15s; }
    .zona.encima { border-color: var(--color-acento); background: #eff6ff; }
    .zona { justify-content: center; }
    .zona.con-archivos { align-items: stretch; justify-content: space-between; padding: 12px; gap: 12px; }
    .fx-elegidos { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
    .fx-elegidos li { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border: 1px solid var(--color-borde); border-radius: 8px; background: #fff; }
    .fx-agregar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px; }
    .fx-agregar-mas, .fx-agregar-acciones { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
    .op { display: inline-block; min-width: 68px; padding: 0 8px; border-radius: 4px; font-size: .74rem; font-weight: 800; text-transform: uppercase; text-align: center; }
    .op-ventas { background: #e0f2fe; color: #075985; }
    .op-compras { background: #fef3c7; color: #92400e; }
    .op-otro { background: #f1f5f9; color: #475569; }
  `,
  template: `
    <app-encabezado
      titulo="Faltantes a Xubio"
      bajada="Los comprobantes de ARCA que faltan en el Libro IVA de Xubio, listos para importarlos. No cambia ningún control."
    />

    <!-- Dos columnas: elegir los AUX a la izquierda, los .zip para Xubio a la derecha -->
    <div class="fx-grilla">
    <section class="mf-panel" aria-labelledby="t-elegir" data-tour="elegir">
      <div class="fx-cab">
        <h2 id="t-elegir" class="m-0 text-lg font-bold">1. Elegí los AUX</h2>
        <p class="mf-sub m-0 mt-1">Los Excel AUX que armó el control, de uno o varios clientes. El cliente, el período y la operación se reconocen solos.</p>
      </div>

      <div class="fx-cuerpo">
      <!-- Los elegidos quedan dentro del cuadro: se pueden soltar más encima -->
      <div
        class="zona"
        [class.con-archivos]="elegidos().length"
        [class.encima]="encima()"
        (dragover)="$event.preventDefault(); encima.set(true)"
        (dragleave)="encima.set(false)"
        (drop)="soltar($event)"
      >
        @if (elegidos().length) {
          <ul class="fx-elegidos">
            @for (e of elegidos(); track e.archivo.name; let i = $index) {
              <li>
                <span class="op" [class]="'op op-' + (e.operacion === 'Ventas' ? 'ventas' : e.operacion === 'Compras' ? 'compras' : 'otro')">{{ e.operacion }}</span>
                <span class="min-w-0 flex-1 text-left">
                  <strong class="block truncate" [title]="e.archivo.name">{{ e.cliente }}</strong>
                  <span class="mf-tenue block truncate text-sm">{{ e.archivo.name }}</span>
                </span>
                <button type="button" class="mf-icono-btn" (click)="quitar(i)" [attr.aria-label]="'Quitar ' + e.archivo.name" [disabled]="convirtiendo()">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" class="h-3.5 w-3.5" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
                </button>
              </li>
            }
          </ul>
          <div class="fx-agregar">
            <span class="fx-agregar-mas">
              <span class="mf-tenue text-sm">Arrastrá más acá o</span>
              <label class="mf-btn mf-btn-suave mf-btn-chico cursor-pointer">
                Agregar
                <input type="file" accept=".xlsx,.xls" multiple class="sr-only" (change)="elegir($event)" />
              </label>
            </span>
            <span class="fx-agregar-acciones">
          <button type="button" class="mf-btn mf-btn-suave" (click)="limpiar()" [disabled]="convirtiendo()">Sacar todos</button>
          <button type="button" class="mf-btn inline-flex items-center gap-2" (click)="convertir()" [disabled]="convirtiendo()" [attr.aria-busy]="convirtiendo()">
            @if (convirtiendo()) {
              <span class="inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden="true"></span>
            }
            {{ convirtiendo() ? 'Convirtiendo…' : 'Convertir ' + elegidos().length + (elegidos().length === 1 ? ' archivo' : ' archivos') }}
          </button>
            </span>
          </div>
        } @else {
          <span class="font-semibold">Arrastrá los AUX acá</span>
          <span class="mf-tenue text-sm">o</span>
          <label class="mf-btn mf-btn-suave cursor-pointer">
            Elegir archivos
            <input type="file" accept=".xlsx,.xls" multiple class="sr-only" (change)="elegir($event)" />
          </label>
        }
      </div>

      @if (error(); as err) { <app-alerta class="mt-3" nivel="error" [mensajes]="[err]" [descartable]="true" (cerrado)="error.set(null)" /> }
      </div>
    </section>

    @if (resultados(); as lista) {
      <section class="mf-panel" aria-labelledby="t-resultado" data-tour="resultado">
        <div class="fx-cab flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0 flex-1">
            <h2 id="t-resultado" class="m-0 text-lg font-bold">2. Cargalos en Xubio</h2>
            <p class="mf-sub m-0 mt-1">Un .zip por operación, listo para «Importar desde Mis Comprobantes (ARCA)» en Xubio. «Ver en Excel» trae los mismos datos para revisarlos antes.</p>
          </div>
          <span class="flex items-center gap-2">
            @if (todosLosZips().length > 1) {
              <button type="button" class="mf-btn mf-btn-suave" (click)="descargarTodos()">Descargar todos ({{ todosLosZips().length }})</button>
            }
            <button type="button" class="mf-icono-btn" (click)="quitarTodos()" aria-label="Quitar todos los resultados" title="Quitar todos">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" class="h-3.5 w-3.5" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
            </button>
          </span>
        </div>
        <div class="fx-cuerpo">
        @if (sinPartidas().length) {
          <app-alerta
            class="mt-3"
            nivel="info"
            titulo="Sin partidas de ARCA no encontradas"
            [mensajes]="sinPartidas()"
          />
        }
        @if (hayNormales() || incluirNormales()) {
          <label class="mt-3 flex items-center gap-2 text-sm">
            <input type="checkbox" [checked]="incluirNormales()" (change)="cambiarIncluirNormales($any($event.target).checked)" [disabled]="convirtiendo() || !elegidos().length" />
            <span>Subir igual las anuladas con nota de crédito y las liquidaciones bancarias<app-ayuda texto="Normalmente no van: una factura anulada con su nota de crédito suma cero, y las liquidaciones del banco ya están en Xubio dentro del extracto. Tildalo solo si en este cliente se cargan aparte." /></span>
          </label>
        }
        <ul class="ej-archivos mt-3">
          @for (r of lista; track claveResultado(r)) {
            @let id = claveResultado(r);
            <li class="!items-start">
              <span class="min-w-0 flex-1">
                <strong class="block truncate" [title]="r.cliente">{{ r.cliente }}@if (r.periodo) { · {{ r.periodo }} }@if (textoControl(r); as c) { <span class="mf-tenue font-normal"> · {{ c }}</span> }</strong>
                @if (r.error) {
                  <span class="text-sm" style="color: var(--color-error)">{{ r.error }}</span>
                } @else {
                  @for (a of r.alertas ?? []; track a) {
                    <span class="mt-1 block rounded-md px-2 py-1 text-sm font-semibold" style="color: var(--color-error); background: #fdecec">⚠ {{ a }}</span>
                  }
                  @for (a of r.avisos; track a) {
                    <span class="block text-sm" style="color: var(--color-acento)">ⓘ {{ a }}</span>
                  }
                  @for (op of operacionesConNormales(r); track op) {
                    <details class="mt-1 text-sm">
                      <summary class="mf-tenue cursor-pointer">Ver {{ r.normales![op]!.length === 1 ? 'la que no se sube' : 'las ' + r.normales![op]!.length + ' que no se suben' }} ({{ op }})</summary>
                      <ul class="m-0 mt-1 list-none p-0">
                        @for (n of r.normales![op]!; track $index) {
                          <li class="mf-tenue !border-0 !p-0 text-xs">{{ n.fecha }} · {{ n.comprobante || n.tipo }} · {{ n.denominacion }} · {{ plata(n.total) }} — {{ n.motivo === 'anulada' ? 'anulada con nota de crédito' : 'está en el extracto del banco' }}</li>
                        }
                      </ul>
                    </details>
                  }
                  @if (operacionesConZip(r).length) {
                    <span class="mt-2 flex flex-wrap items-center gap-2 text-sm">
                      <label class="mf-tenue" [attr.for]="'empresa-' + id">Empresa en Xubio<app-ayuda texto="En qué empresa de Xubio se importa. Se elige sola si el nombre coincide con el cliente; revisala antes de cargar." /></label>
                      <select class="mf-input !w-auto !py-1 text-sm" [id]="'empresa-' + id" [ngModel]="empresaDe(r)" (ngModelChange)="elegirEmpresa(r, $event)">
                        <option value="">Elegí el cliente…</option>
                        @for (c of clientes.lista(); track c.nombre) { <option [value]="c.nombre">{{ c.nombre }}</option> }
                      </select>
                    </span>
                    @for (op of operacionesConZip(r); track op) {
                      @let clave = claveOperacion(r, op);
                      @let reg = registro()[clave];
                      <span class="mt-2 flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-white px-2.5 py-2 text-sm">
                        <span class="min-w-0 flex-1">
                          <strong>{{ op }}</strong>
                          <span class="mf-tenue"> · {{ r.partidas[op] }} {{ r.partidas[op] === 1 ? 'partida' : 'partidas' }}</span>
                        </span>
                        @if (reg?.estado === 'importado') {
                          <span class="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-semibold" style="color: var(--color-ok-texto); background: #e7f6ec">✓ Importado en Xubio el {{ fechaCorta(reg!.fecha) }}</span>
                          <button type="button" class="mf-btn-link text-xs" (click)="cargarEnXubio(r, op)" [disabled]="!!cargandoXubio() || !empresaDe(r)" title="Avisa antes: si ya se importó, se duplica">Cargar de nuevo</button>
                        } @else if (reg?.estado === 'pendiente') {
                          <span class="inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-semibold" style="color: #92400e; background: #fef3c7">⏳ Falta apretar «Importar» en Xubio<app-ayuda texto="El bot deja el archivo cargado pero no lo importa: lo confirmás vos en la ventana de Chrome. Cuando lo hagas, tocá «Ya lo importé»." lado="izquierda" /></span>
                          <button type="button" class="mf-btn mf-btn-chico" (click)="marcarImportado(r, op)">Ya lo importé</button>
                          <button type="button" class="mf-btn-link text-xs" (click)="olvidar(r, op)" title="No llegaste a importarlo: se puede volver a cargar">No lo importé</button>
                        } @else {
                          <button type="button" class="mf-btn mf-btn-chico inline-flex items-center gap-2" (click)="cargarEnXubio(r, op)" [disabled]="!!cargandoXubio() || !empresaDe(r)" [attr.aria-busy]="cargandoXubio() === clave" [title]="empresaDe(r) ? '' : 'Elegí primero la empresa en Xubio'">
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
                      @if (reg?.estado === 'importado') {
                        <span class="mf-tenue mt-1 block text-sm">Para confirmar que quedó bien, <a routerLink="/control" class="mf-btn-link">corré un control nuevo de {{ r.periodo || 'ese período' }}</a>: estas partidas ya no tendrían que aparecer como faltantes.</span>
                      } @else if (estadoXubio()[clave]; as est) {
                        <span class="mt-1 block text-sm" [style.color]="est.ok ? 'var(--color-ok-texto)' : 'var(--color-error)'">{{ est.texto }}</span>
                      }
                    }
                  }
                }
              </span>
              <span class="flex shrink-0 items-center gap-2">
                @if (r.excel) {
                  <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="bajar(r.excel, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')" [attr.aria-label]="'Ver en Excel ' + r.excel.nombre" title="Los mismos datos en Excel, con el CAE y el CUIT completos">Ver en Excel</button>
                }
                <button type="button" class="mf-icono-btn" (click)="quitarResultado(r)" [attr.aria-label]="'Quitar ' + r.cliente" title="Quitar">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" class="h-3.5 w-3.5" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
                </button>
              </span>
            </li>
          }
        </ul>
        </div>
      </section>
    } @else {
      <section class="mf-panel" aria-labelledby="t-resultado-vacio" data-tour="resultado">
        <div class="fx-cab">
          <h2 id="t-resultado-vacio" class="m-0 text-lg font-bold">2. Cargalos en Xubio</h2>
          <p class="mf-sub m-0 mt-1">Un .zip por operación, listo para importar en Xubio, o el bot lo carga por vos.</p>
        </div>
        <div class="fx-cuerpo">
          <div class="fx-vacio">
            <strong>Acá aparecen los .zip listos para Xubio</strong>
            <span class="text-sm">Elegí los AUX en el paso 1 y apretá «Convertir».</span>
            <span class="mf-tenue text-xs">¿De dónde salen? Cada control deja el AUX en la carpeta del cliente: <em>período › Control de facturas › Nº control</em>. Usá el del último control. Si todavía no corriste uno, <a routerLink="/control" class="mf-btn-link">empezá por Nuevo control</a>.</span>
          </div>
        </div>
      </section>
    }
    </div>
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
  /** Subir igual las anuladas con NC y las liquidaciones bancarias. */
  protected readonly incluirNormales = signal(false);
  /** Empresa de Xubio elegida a mano por resultado (si no, la que coincide con el nombre). */
  private readonly empresas = signal<Record<string, string>>({});
  /** «resultado|operación» que se está subiendo y su mensaje. */
  protected readonly cargandoXubio = signal<string | null>(null);
  protected readonly estadoXubio = signal<Record<string, { ok: boolean; texto: string }>>({});
  /** Qué se cargó en Xubio y si ya se apretó «Importar» (queda en este navegador). */
  protected readonly registro = signal<Record<string, RegistroImportacion>>(leerRegistro());

  constructor() {
    void this.clientes.cargar();
  }

  /** Si cierra la pestaña con algo cargado sin importar, el navegador pregunta. */
  @HostListener('window:beforeunload', ['$event'])
  protected antesDeCerrar(ev: BeforeUnloadEvent): void {
    if (this.pendientes().length) ev.preventDefault();
  }

  /** Para el guard de la ruta: si quedó algo sin importar, se lo recuerda antes de salir. */
  async puedeSalir(): Promise<boolean> {
    const pendientes = this.pendientes();
    if (!pendientes.length) return true;
    return this.alertas.confirmar(
      'Te falta apretar «Importar» en Xubio',
      'Cargaste archivos en Xubio que todavía no marcaste como importados. Si no apretás «Importar» en la ventana de Chrome, no quedan cargados.',
      'Salir igual',
      pendientes,
    );
  }

  /** Lo que se cargó y falta confirmar, de los resultados que están en pantalla. */
  private pendientes(): string[] {
    const reg = this.registro();
    return (this.resultados() ?? []).flatMap((r) =>
      this.operacionesConZip(r)
        .filter((op) => reg[this.claveOperacion(r, op)]?.estado === 'pendiente')
        .map((op) => `${r.cliente}${r.periodo ? ' · ' + r.periodo : ''} · ${op}`),
    );
  }

  protected claveResultado(r: ResultadoConversion): string {
    return `${normalizar(r.cliente)}|${r.periodo}`;
  }

  protected claveOperacion(r: ResultadoConversion, op: 'Ventas' | 'Compras'): string {
    return `${this.claveResultado(r)}|${op}`;
  }

  protected textoControl(r: ResultadoConversion): string {
    const numeros = [...new Set(Object.values(r.control ?? {}).filter((n): n is number => !!n))];
    return numeros.length === 1 ? `${ordinal(numeros[0])} control` : '';
  }

  protected empresaDe(r: ResultadoConversion): string {
    const elegida = this.empresas()[this.claveResultado(r)];
    if (elegida !== undefined) return elegida;
    const buscado = normalizar(r.cliente);
    const lista = this.clientes.lista();
    const exacta = lista.find((c) => normalizar(c.nombre) === buscado);
    const parecida = lista.find((c) => normalizar(c.nombre).includes(buscado) || buscado.includes(normalizar(c.nombre)));
    return (exacta ?? parecida)?.nombre ?? '';
  }

  protected elegirEmpresa(r: ResultadoConversion, nombre: string): void {
    this.empresas.update((m) => ({ ...m, [this.claveResultado(r)]: nombre }));
  }

  protected operacionesConZip(r: ResultadoConversion): ('Ventas' | 'Compras')[] {
    return (['Compras', 'Ventas'] as const).filter((op) => r.zips[op]);
  }

  protected operacionesConNormales(r: ResultadoConversion): ('Ventas' | 'Compras')[] {
    return this.incluirNormales() ? [] : (['Compras', 'Ventas'] as const).filter((op) => r.normales?.[op]?.length);
  }

  protected readonly hayNormales = computed(() => (this.resultados() ?? []).some((r) => Object.values(r.normales ?? {}).some((l) => l?.length)));

  protected plata(n: number | null): string {
    return n === null || n === undefined ? '' : `$ ${n.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }

  protected fechaCorta(iso: string): string {
    const d = new Date(iso);
    return isNaN(d.getTime()) ? '' : d.toLocaleDateString('es-AR', { day: '2-digit', month: '2-digit' });
  }

  /**
   * El bot carga el archivo en «Importar desde Mis Comprobantes (ARCA)» y se
   * detiene. Antes: si ya se importó, avisa que se duplicaría; después, muestra
   * las partidas y pide tildar que se revisaron.
   */
  protected async cargarEnXubio(r: ResultadoConversion, op: 'Ventas' | 'Compras'): Promise<void> {
    const empresa = this.empresaDe(r);
    const zip = r.zips[op];
    const clave = this.claveOperacion(r, op);
    if (!zip || !empresa || this.cargandoXubio()) return;

    const previo = this.registro()[clave];
    if (previo?.estado === 'importado') {
      const igual = await this.alertas.confirmarAccionPeligrosa(
        'Esto ya lo importaste',
        `Las partidas de ${op} de ${r.cliente}${r.periodo ? ' · ' + r.periodo : ''} ya las marcaste como importadas el ${this.fechaCorta(previo.fecha)}. Si las cargás de nuevo se duplican en Xubio. Si siguen apareciendo como faltantes, revisá primero en Xubio si la importación quedó hecha.`,
        'Cargar igual',
      );
      if (!igual) return;
    }

    const filas = r.filas?.[op] ?? [];
    const n = r.partidas[op] ?? filas.length;
    const seguir = await this.alertas.revisarAntesDeSeguir({
      titulo: `Revisá las partidas de ${op}`,
      mensaje: `Se van a cargar ${n} ${n === 1 ? 'partida' : 'partidas'} en Xubio, en la empresa «${empresa}». El bot no aprieta «Importar»: te deja la ventana de Chrome abierta para que lo confirmes vos.`,
      alertas: (r.alertas ?? []).filter((a) => a.startsWith(op)),
      columnas: ['Fecha', 'Comprobante', 'Razón social', 'CUIT', 'Total'],
      filas: filas.map((f) => [f.fecha, f.comprobante || f.tipo, f.denominacion, formatearCuit(f.cuit), f.total ?? '']),
      tilde: `Revisé ${n === 1 ? 'la partida' : `las ${n} partidas`} y van a Xubio`,
      textoConfirmar: 'Abrir Xubio',
    });
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
        this.guardarRegistro(clave, { estado: 'pendiente', fecha: new Date().toISOString(), partidas: n, control: r.control?.[op] ?? null });
        this.estadoXubio.update((m) => ({
          ...m,
          [clave]: { ok: true, texto: `Archivo de ${op} cargado en Xubio (${empresa}). Apretá «Importar» en la ventana de Chrome y después «Ya lo importé».` },
        }));
        void this.alertas.mostrarAviso(
          'Falta apretar «Importar»',
          `El archivo de ${op} quedó cargado en Xubio, pero no está importado. En la ventana de Chrome revisalo y apretá «Importar».`,
          ['Después volvé acá y apretá «Ya lo importé».', 'Si no lo importás, esas partidas siguen faltando en el Libro IVA.'],
        );
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

  protected marcarImportado(r: ResultadoConversion, op: 'Ventas' | 'Compras'): void {
    const clave = this.claveOperacion(r, op);
    const previo = this.registro()[clave];
    this.guardarRegistro(clave, { ...(previo ?? { partidas: r.partidas[op] ?? 0, control: r.control?.[op] ?? null }), estado: 'importado', fecha: new Date().toISOString() });
    this.toast.ok('Importado', `${op} de ${r.cliente} marcado como importado. Corré un control nuevo para confirmarlo.`);
  }

  protected olvidar(r: ResultadoConversion, op: 'Ventas' | 'Compras'): void {
    const clave = this.claveOperacion(r, op);
    this.registro.update((m) => {
      const { [clave]: _, ...resto } = m;
      escribirRegistro(resto);
      return resto;
    });
    this.estadoXubio.update((m) => {
      const { [clave]: _, ...resto } = m;
      return resto;
    });
  }

  private guardarRegistro(clave: string, valor: RegistroImportacion): void {
    this.registro.update((m) => {
      const nuevo = { ...m, [clave]: valor };
      escribirRegistro(nuevo);
      return nuevo;
    });
  }

  protected async quitarResultado(r: ResultadoConversion): Promise<void> {
    if (!(await this.confirmarQuitar([r]))) return;
    const clave = this.claveResultado(r);
    const quedan = (this.resultados() ?? []).filter((x) => this.claveResultado(x) !== clave);
    this.resultados.set(quedan.length ? quedan : null);
  }

  protected async quitarTodos(): Promise<void> {
    if (!(await this.confirmarQuitar(this.resultados() ?? []))) return;
    this.resultados.set(null);
    this.estadoXubio.set({});
  }

  /** Sacar de la pantalla algo cargado sin importar: se recuerda antes. */
  private async confirmarQuitar(lista: ResultadoConversion[]): Promise<boolean> {
    const reg = this.registro();
    const pendientes = lista.flatMap((r) =>
      this.operacionesConZip(r)
        .filter((op) => reg[this.claveOperacion(r, op)]?.estado === 'pendiente')
        .map((op) => `${r.cliente} · ${op}`),
    );
    if (!pendientes.length) return true;
    return this.alertas.confirmar(
      'Te falta apretar «Importar» en Xubio',
      'Esto está cargado en Xubio pero no lo marcaste como importado. Si lo sacás de la pantalla, acordate de apretar «Importar» en la ventana de Chrome.',
      'Quitar igual',
      pendientes,
    );
  }

  protected cambiarIncluirNormales(valor: boolean): void {
    this.incluirNormales.set(valor);
    void this.convertir();
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
      const r = await this.api.convertirAux(archivos, this.incluirNormales());
      this.resultados.set(r.resultados);
      this.estadoXubio.set({});
      // Lo que hay que mirar antes de cargar (AUX viejo, ARCA trajo otro período).
      const alertas = r.resultados.flatMap((x) => (x.alertas ?? []).map((a) => `${x.cliente}${x.periodo ? ' · ' + x.periodo : ''}: ${a}`));
      if (alertas.length) void this.alertas.mostrarAviso('Revisá antes de cargar en Xubio', 'Estos AUX pueden traer partidas que no corresponden:', alertas);
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
