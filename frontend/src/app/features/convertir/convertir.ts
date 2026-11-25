import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { descargarBlob } from '../../core/descargar';
import { ArchivoLeido, ComprobanteExtraido, ResultadoConversion, mensajeDeError } from '../../core/models';
import { ApiService } from '../../core/services/api.service';
import { ClientesStore } from '../../core/services/clientes.store';
import { PerspectivaService } from '../../core/services/perspectiva.service';
import { AlertasService } from '../../core/ui/alertas.service';
import { Alerta } from '../../core/ui/alerta';
import { Ayuda } from '../../core/ui/ayuda';
import { Encabezado } from '../../core/ui/encabezado';
import { ToastService } from '../../core/ui/toast';
import { Precarga, borrarPrecarga, leerPrecarga } from '../ejecucion/seleccion';
import { abreviarTipo } from '../ejecucion/ventana-errores';
import { validarComprobante } from './comprobantes';
import { RevisionComprobantes } from './revision';

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
 *
 * Desde un control («⬆ Subir a Xubio» en Ejecuciones) llega precargado: el AUX de ese
 * control sin los comprobantes destildados, más las correcciones elegidas en «Error».
 * Elegir los AUX a mano sigue disponible.
 */
@Component({
  selector: 'app-convertir',
  imports: [Encabezado, Alerta, FormsModule, RouterLink, Ayuda, RevisionComprobantes],
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
    .fx-lista { width: 100%; border-collapse: collapse; table-layout: fixed; font-size: .82rem; }
    .fx-lista th { text-align: left; font-size: .74rem; font-weight: 700; color: #64748b; padding: 4px 6px; border-bottom: 1px solid #e2e8f0; white-space: nowrap; }
    .fx-lista td { padding: 4px 6px; border-bottom: 1px solid #f1f5f9; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .fx-lista .num { text-align: right; font-variant-numeric: tabular-nums; }
    .fx-lista .mono { font-family: Consolas, 'Cascadia Mono', monospace; }
    .fx-detalle { margin-top: 8px; }
    .fx-detalle > summary { cursor: pointer; font-size: .85rem; font-weight: 700; color: var(--color-acento); }
    .fx-correcciones { display: flex; flex-direction: column; gap: 4px; margin: 6px 0 0; font-size: .84rem; line-height: 1.4; }
    .fx-cab-der { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; padding-bottom: 14px; border-bottom: 1px solid var(--color-borde); }
    .fx-cab-der .op, .fx-res-cab .op { padding: 3px 8px; min-width: 0; }
    .fx-resultado + .fx-resultado, app-alerta + .fx-resultado { margin-top: 18px; padding-top: 18px; border-top: 1px dashed var(--color-borde); }
    .fx-res-cab { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; margin-top: 14px; font-size: 1rem; }
    .fx-op-titulo { margin: 10px 0 6px; font-size: .82rem; font-weight: 700; color: var(--color-tenue); }
    .fx-op-titulo:first-of-type { margin-top: 0; }
    .fx-subir-lista { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; }
    .fx-cargando { display: flex; align-items: center; gap: 10px; padding: 40px 0; justify-content: center; color: var(--color-tenue); font-weight: 600; }
    .fx-pasos { list-style: none; margin: 18px 0 0; padding: 0; display: flex; flex-direction: column; gap: 22px; }
    .fx-paso { display: grid; grid-template-columns: 30px minmax(0, 1fr); gap: 12px; }
    .fx-num { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 999px; background: #eff6ff; color: var(--color-acento); font-weight: 800; font-size: .9rem; }
    .fx-paso h3 { display: flex; align-items: center; gap: 4px; margin: 4px 0 8px; font-size: .98rem; font-weight: 700; text-transform: none; letter-spacing: normal; color: var(--color-texto, #0f172a); }
    .fx-caja { border: 1px solid var(--color-borde); border-radius: 10px; overflow-x: auto; background: #fff; }
    .fx-caja .fx-lista th { background: #f8fafc; padding: 8px 10px; }
    .fx-caja .fx-lista td { padding: 7px 10px; }
    .fx-caja .fx-lista tbody tr:last-child td { border-bottom: 0; }
    .fx-empresa { max-width: 420px; }
    .fx-subir { padding: 10px 18px; font-size: .95rem; }
    .fx-hecho { display: inline-flex; align-items: center; padding: 8px 12px; border-radius: 8px; font-weight: 700; color: var(--color-ok-texto); background: #e7f6ec; }
    .fx-pie { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; margin-top: 22px; padding-top: 14px; border-top: 1px solid var(--color-borde); }
    .fx-link { border: 0; background: none; padding: 0; color: var(--color-tenue); font-size: .85rem; text-decoration: underline; cursor: pointer; }
    .fx-link:hover { color: var(--color-acento); }
    .fx-correcciones p { margin: 0; padding: 4px 8px; border-left: 3px solid #fca5a5; background: #fffafa; border-radius: 4px; }
  `,
  template: `
    <app-encabezado
      titulo="Subir a Xubio lo que falta"
      bajada="Los comprobantes que están en ARCA pero no en el Libro IVA de Xubio, listos para cargarlos. No cambia ningún control."
    />

    <!-- Izquierda: elegir los Excel del control (siempre). Derecha: lo listo para Xubio; si se vino desde un control, ya armado -->
    <div class="fx-grilla">
    <section class="mf-panel" aria-labelledby="t-elegir" data-tour="elegir">
      <div class="fx-cab">
        <h2 id="t-elegir" class="m-0 text-lg font-bold">1. Elegí los archivos<app-ayuda texto="Los Excel «AUX - Control de facturas…» del control van directo. Las facturas en PDF, fotos, capturas, CSV o planillas propias se leen y las revisás antes de subirlas (de las fotos se lee el QR de ARCA y el texto con OCR)." /></h2>
        <p class="mf-sub m-0 mt-1">Los Excel del control («AUX - Control de facturas…») o comprobantes sueltos: PDF, fotos (JPG, PNG), capturas, CSV o planillas. Pueden ser de varios clientes.</p>
      </div>

      <div class="fx-cuerpo">
      <!-- Los elegidos quedan dentro del cuadro: se pueden soltar más encima -->
      <div
        class="zona"
        [class.con-archivos]="elegidos().length || precarga()"
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
                <input type="file" [accept]="acepta" multiple class="sr-only" (change)="elegir($event)" />
              </label>
            </span>
            <span class="fx-agregar-acciones">
          <button type="button" class="mf-btn mf-btn-suave" (click)="limpiar()" [disabled]="convirtiendo()">Sacar todos</button>
          <button type="button" class="mf-btn inline-flex items-center gap-2" (click)="convertir()" [disabled]="convirtiendo()" [attr.aria-busy]="convirtiendo()">
            @if (convirtiendo()) {
              <span class="inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden="true"></span>
            }
            {{ convirtiendo() ? 'Leyendo…' : (hayParaLeer() ? 'Leer archivos (' : 'Preparar para Xubio (') + elegidos().length + ')' }}
          </button>
            </span>
          </div>
        } @else if (precarga(); as p) {
          <!-- Vino del control: el mismo aspecto que un Excel elegido a mano -->
          <ul class="fx-elegidos">
            <li>
              <span class="op" [class]="'op op-' + (p.operacion === 'Ventas' ? 'ventas' : 'compras')">{{ p.operacion }}</span>
              <span class="min-w-0 flex-1 text-left">
                <strong class="block truncate" [title]="p.aux">{{ p.cliente }}</strong>
                <span class="mf-tenue block truncate text-sm">{{ p.aux }}</span>
              </span>
              <button type="button" class="mf-icono-btn" (click)="quitarPrecarga()" [attr.aria-label]="'Quitar ' + p.aux" [disabled]="convirtiendo() || !!cargandoXubio()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" class="h-3.5 w-3.5" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
              </button>
            </li>
          </ul>
          <div class="fx-agregar">
            <span class="fx-agregar-mas">
              <span class="mf-tenue text-sm">Viene del control. Para usar otros, arrastralos acá o</span>
              <label class="mf-btn mf-btn-suave mf-btn-chico cursor-pointer">
                Elegir archivos
                <input type="file" [accept]="acepta" multiple class="sr-only" (change)="elegir($event)" />
              </label>
            </span>
          </div>
        } @else {
          <span class="font-semibold">Arrastrá los archivos acá</span>
          <span class="mf-tenue text-sm">Excel del control, PDF, fotos, CSV o planillas</span>
          <span class="mf-tenue text-sm">o</span>
          <label class="mf-btn mf-btn-suave cursor-pointer">
            Elegir archivos
            <input type="file" [accept]="acepta" multiple class="sr-only" (change)="elegir($event)" />
          </label>
        }
      </div>

      @if (!precarga() && error(); as err) { <app-alerta class="mt-3" nivel="error" [mensajes]="[err]" [descartable]="true" (cerrado)="error.set(null)" /> }
      </div>
    </section>

    <!-- Derecha: lo listo para Xubio, paso a paso. Igual venga del botón rápido del control o de los Excel elegidos a mano -->
    <section class="mf-panel" aria-labelledby="t-resultado" data-tour="resultado">
      <div class="fx-cab-der">
        <div class="min-w-0 flex-1">
          <h2 id="t-resultado" class="m-0 text-lg font-bold">2. Subilos a Xubio<app-ayuda texto="«Subir a Xubio» abre Xubio en esta PC y deja el archivo listo en «Importar desde Mis Comprobantes (ARCA)». El bot no aprieta «Importar»: lo confirmás vos en la ventana de Chrome." /></h2>
          <p class="mf-sub m-0 mt-1">El bot los carga en Xubio por vos, o descargás el archivo para importarlo a mano.</p>
        </div>
        @if (precarga(); as p) {
          <a class="mf-btn mf-btn-suave mf-btn-chico" [routerLink]="['/ejecuciones', p.ejecucionId]" title="Volvé al control para tildar o destildar comprobantes">← Cambiar cuáles van</a>
        } @else if (todosLosZips().length > 1) {
          <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="descargarTodos()">Descargar todos ({{ todosLosZips().length }})</button>
        }
      </div>

      <div class="fx-cuerpo">
        @if (convirtiendo()) {
          <div class="fx-cargando" aria-live="polite">
            <span class="inline-block h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-slate-300 border-t-[var(--color-acento)]" aria-hidden="true"></span>
            Leyendo los archivos…
          </div>
        } @else if (precarga() && error()) {
          <app-alerta class="mt-4" nivel="error" [mensajes]="[error()!]" [descartable]="true" (cerrado)="error.set(null)" />
        } @else if (resultados(); as lista) {
          @if (sinPartidas().length) {
            <app-alerta class="mt-4" nivel="info" titulo="Nada para subir" [mensajes]="sinPartidas()" />
          }
          @for (r of lista; track $index) {
            @let i = $index;
            @if (r.error) {
              <app-alerta class="mt-4" nivel="error" [titulo]="r.cliente" [mensajes]="[r.error]" />
            } @else if (operacionesConZip(r).length) {
              <div class="fx-resultado">
                <div class="fx-res-cab">
                  @for (op of operacionesConZip(r); track op) {
                    <span class="op" [class]="'op op-' + (op === 'Ventas' ? 'ventas' : 'compras')">{{ op }}</span>
                  }
                  <strong class="min-w-0 flex-1 truncate" [title]="r.cliente">{{ r.cliente }}@if (r.periodo) { <span class="mf-tenue font-normal"> · {{ r.periodo }}</span> }</strong>
                  @if (precarga(); as p) {
                    @if (p.partidas.length < p.total) { <span class="mf-tenue text-sm">Dejaste afuera {{ p.total - p.partidas.length }}</span> }
                  }
                </div>
                <ol class="fx-pasos">
                  <li class="fx-paso">
                    <span class="fx-num" aria-hidden="true">1</span>
                    <div class="min-w-0">
                      <h3>Revisá lo que se sube</h3>
                      @for (op of operacionesConZip(r); track op) {
                        @if (operacionesConZip(r).length > 1) {
                          <p class="fx-op-titulo">{{ op }} · {{ r.partidas[op] }} {{ r.partidas[op] === 1 ? 'comprobante' : 'comprobantes' }}</p>
                        }
                        @if (filasDe(r, op).length) {
                          <div class="fx-caja">
                            <table class="fx-lista">
                              <colgroup><col style="width: 92px" /><col style="width: 54px" /><col style="width: 150px" /><col /><col style="width: 104px" /></colgroup>
                              <thead><tr><th>Fecha</th><th>Tipo</th><th>Número</th><th>{{ op === 'Compras' ? 'Proveedor' : 'Cliente' }}</th><th class="num">Total</th></tr></thead>
                              <tbody>
                                @for (f of filasDe(r, op); track $index) {
                                  <tr><td>{{ f.fecha }}</td><td>{{ f.tipo }}</td><td class="mono">{{ f.comprobante }}</td><td [title]="f.razon">{{ f.razon }}</td><td class="num">{{ f.total }}</td></tr>
                                }
                              </tbody>
                            </table>
                          </div>
                        } @else {
                          <p class="mf-tenue m-0 text-sm">{{ r.partidas[op] }} {{ r.partidas[op] === 1 ? 'comprobante' : 'comprobantes' }} de {{ op }}. Para verlos, tocá «Ver en Excel».</p>
                        }
                      }
                      @for (a of r.avisos; track a) {
                        <p class="m-0 mt-2 text-sm" style="color: var(--color-acento)">ⓘ {{ a }}</p>
                      }
                      @if (precargaDe(r); as p) {
                        @if (p.correcciones.length) {
                          <details class="fx-detalle">
                            <summary>Además, {{ p.correcciones.length }} {{ p.correcciones.length === 1 ? 'corrección' : 'correcciones' }} para hacer a mano en Xubio</summary>
                            <div class="fx-correcciones">
                              @for (c of p.correcciones; track $index) {
                                <p><strong class="mono">{{ c.comprobante }}</strong>@if (c.razon) { · {{ c.razon }} } — {{ c.texto }}</p>
                              }
                            </div>
                          </details>
                        }
                      }
                    </div>
                  </li>

                  <li class="fx-paso">
                    <span class="fx-num" aria-hidden="true">2</span>
                    <div class="min-w-0">
                      <h3><label [attr.for]="'empresa-' + i">Confirmá la empresa en Xubio</label><app-ayuda texto="En qué empresa de Xubio se cargan. Se elige sola si el nombre coincide con el cliente." /></h3>
                      <select class="mf-input fx-empresa" [id]="'empresa-' + i" [ngModel]="empresaDe(r, i)" (ngModelChange)="elegirEmpresa(i, $event)">
                        <option value="">Elegí la empresa…</option>
                        @for (c of clientes.lista(); track c.nombre) { <option [value]="c.nombre">{{ c.nombre }}</option> }
                      </select>
                    </div>
                  </li>

                  <li class="fx-paso">
                    <span class="fx-num" aria-hidden="true">3</span>
                    <div class="min-w-0">
                      <h3>Subilos<app-ayuda texto="Se abre Xubio en esta PC y el archivo queda cargado en «Importar desde Mis Comprobantes (ARCA)». El bot no aprieta «Importar»: lo confirmás vos en la ventana de Chrome." /></h3>
                      <div class="fx-subir-lista">
                        @for (op of operacionesConZip(r); track op) {
                          @let clave = i + '-' + op;
                          @if (cargados()[clave]) {
                            <span class="fx-hecho">✓ {{ op }} cargado en Xubio · falta apretar «Importar» en Chrome</span>
                          } @else {
                            <button type="button" class="mf-btn fx-subir inline-flex items-center gap-2" (click)="cargarEnXubio(r, i, op)" [disabled]="!!cargandoXubio() || !empresaDe(r, i) || !perspectiva.canEdit()" [attr.aria-busy]="cargandoXubio() === clave" [title]="perspectiva.motivoBloqueo() ?? (empresaDe(r, i) ? '' : 'Elegí primero la empresa en Xubio')">
                              @if (cargandoXubio() === clave) {
                                <span class="inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden="true"></span>
                                Abriendo Xubio…
                              } @else {
                                ⬆ Subir {{ r.partidas[op] }} {{ r.partidas[op] === 1 ? 'comprobante' : 'comprobantes' }} de {{ op }}
                              }
                            </button>
                          }
                          @if (estadoXubio()[clave]; as est) {
                            <p class="m-0 text-sm" [style.color]="est.ok ? 'var(--color-ok-texto)' : 'var(--color-error)'">{{ est.texto }}</p>
                          }
                        }
                        <span class="mf-tenue text-sm">Vos confirmás con «Importar» en la ventana que se abre.</span>
                      </div>
                    </div>
                  </li>
                </ol>

                <div class="fx-pie">
                  <span class="mf-tenue text-sm">¿Preferís hacerlo a mano?</span>
                  @for (op of operacionesConZip(r); track op) {
                    <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="bajar(r.zips[op]!, 'application/zip')" title="El .zip que se importa en Xubio desde «Importar desde Mis Comprobantes (ARCA)»">Descargar archivo{{ operacionesConZip(r).length > 1 ? ' de ' + op : '' }}</button>
                  }
                  @if (r.excel) {
                    <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="bajar(r.excel, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')" title="Los mismos datos en Excel, con el CAE y el CUIT completos">Ver en Excel</button>
                  }
                  @if (precarga()) {
                    <button type="button" class="fx-link ml-auto" (click)="quitarPrecarga()" [disabled]="!!cargandoXubio()">Usar otros archivos</button>
                  }
                </div>
              </div>
            }
          }
        } @else {
          <div class="fx-vacio">
            @if (revision().length) {
              <strong>Revisá los comprobantes leídos</strong>
              <span class="text-sm">Están abajo: corregí lo que haga falta y tocá «Preparar para Xubio».</span>
            } @else {
              <strong>Acá aparece lo que falta subir</strong>
              <span class="text-sm">Elegí los archivos en el paso 1 y tocá «Preparar para Xubio».</span>
            }
          </div>
        }
      </div>
    </section>
    </div>

    @if (revision().length) {
      <app-revision-comprobantes [(comprobantes)]="revision" [informe]="informe()" [clientes]="nombresClientes()" [preparando]="preparando()" (preparar)="prepararComprobantes()" />
    }
  `,
})
export class ConvertirPantalla {
  private readonly api = inject(ApiService);
  private readonly toast = inject(ToastService);
  private readonly alertas = inject(AlertasService);
  protected readonly clientes = inject(ClientesStore);
  /** Empleado: convierte y descarga, pero «Cargar en Xubio» queda bloqueado. */
  protected readonly perspectiva = inject(PerspectivaService);

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
  /** «⬆ Subir a Xubio» desde un control: lo tildado ahí, ya convertido en el paso 2. */
  protected readonly precarga = signal<Precarga | null>(null);
  /** Lo que acepta el paso 1: los AUX del control y comprobantes sueltos. */
  protected readonly acepta = '.xlsx,.xls,.csv,.pdf,.jpg,.jpeg,.png';
  /** Comprobantes leídos de PDF, fotos, CSV y planillas, para revisar antes de prepararlos. */
  protected readonly revision = signal<ComprobanteExtraido[]>([]);
  protected readonly informe = signal<ArchivoLeido[]>([]);
  protected readonly preparando = signal(false);
  /** Lo convertido de los AUX: queda junto a lo que se prepare de la revisión. */
  private resultadosAux: ResultadoConversion[] = [];
  protected readonly nombresClientes = computed(() => this.clientes.lista().map((c) => c.nombre));
  /** Hay algo que no es un AUX del control (por el nombre): se lee y se revisa antes. */
  protected readonly hayParaLeer = computed(() => this.elegidos().some((e) => !NOMBRE_AUX.test(e.archivo.name)));

  constructor() {
    void this.clientes.cargar();
    const p = inject(ActivatedRoute).snapshot.queryParamMap.get('desde') === 'control' ? leerPrecarga() : null;
    if (p) {
      this.precarga.set(p);
      void this.convertirDesdeControl(p);
    }
  }

  /** El AUX del control (sin los destildados) directo al paso 2, sin arrastrar archivos. */
  private async convertirDesdeControl(p: Precarga): Promise<void> {
    this.convirtiendo.set(true);
    this.error.set(null);
    try {
      const contenido = await base64De(await this.api.archivo(p.ejecucionId, p.indice, p.aux), p.aux);
      const r = await this.api.convertirAux([{ nombre: p.aux, contenido, excluir: p.excluir }]);
      if (this.precarga() !== p) return;
      this.resultados.set(r.resultados);
      this.cargados.set({});
      this.estadoXubio.set({});
    } catch (err) {
      this.error.set(mensajeDeError(err, 'No se pudo leer el AUX del control. Elegilo a mano.'));
    } finally {
      this.convirtiendo.set(false);
    }
  }

  /** Lo que vino del control, si este resultado es ese AUX. */
  protected precargaDe(r: ResultadoConversion): Precarga | null {
    const p = this.precarga();
    return p && r.archivos.includes(p.aux) ? p : null;
  }

  /**
   * Los comprobantes que van en el .zip de esa operación. Una sola fuente para el
   * botón rápido del control y para los Excel elegidos a mano: lo que devuelve el
   * servidor al convertir (ya sin los destildados), o sea exactamente lo que se sube.
   */
  protected filasDe(r: ResultadoConversion, op: 'Ventas' | 'Compras'): { fecha: string; tipo: string; comprobante: string; razon: string; total: string }[] {
    return (r.filas?.[op] ?? []).map((f) => ({
      ...f,
      tipo: abreviarTipo(f.tipo),
      total: typeof f.total === 'number' ? f.total.toLocaleString('es-AR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : f.total,
    }));
  }

  protected quitarPrecarga(): void {
    borrarPrecarga();
    this.precarga.set(null);
    this.resultados.set(null);
    this.error.set(null);
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
    if (!this.perspectiva.canEdit()) return;
    const empresa = this.empresaDe(r, i);
    const zip = r.zips[op];
    const clave = `${i}-${op}`;
    if (!zip || !empresa || this.cargandoXubio() || this.cargados()[clave]) return;
    const seguir = await this.alertas.confirmar(
      `Subir ${op} a Xubio`,
      `Se abre Xubio en esta PC, en la empresa «${empresa}», y se carga el archivo en Importar desde Mis Comprobantes (ARCA).`,
      'Abrir Xubio',
      [
        `${r.partidas[op]} ${r.partidas[op] === 1 ? 'comprobante' : 'comprobantes'} de ${op}.`,
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
      .map((r) => `${r.cliente}${r.periodo ? ' · ' + r.periodo : ''}: todo lo de ARCA ya está en el Libro IVA de Xubio (${Object.keys(r.partidas).join(' y ')}). No se generó archivo.`),
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
    const validos = archivos.filter((a) => FORMATOS.test(a.name));
    if (validos.length < archivos.length) this.error.set('Se aceptan Excel, CSV, PDF y fotos o capturas (JPG, PNG).');
    const ya = new Set(this.elegidos().map((e) => e.archivo.name));
    const nuevos = validos.filter((a) => !ya.has(a.name)).map((archivo) => {
      const m = NOMBRE_AUX.exec(archivo.name);
      const base = archivo.name.replace(/\.[^.]+$/, '');
      return { archivo, operacion: m ? capitalizar(m[1]) : etiquetaDe(archivo.name), cliente: m ? m[3] : base };
    });
    this.elegidos.update((l) => [...l, ...nuevos]);
    if (this.precarga()) this.quitarPrecarga();
    this.resultados.set(null);
    this.empresas.set({});
    this.estadoXubio.set({});
    this.cargados.set({});
    this.revision.set([]);
    this.informe.set([]);
  }

  protected quitar(i: number): void {
    this.elegidos.update((l) => l.filter((_, j) => j !== i));
  }

  protected limpiar(): void {
    this.elegidos.set([]);
    this.resultados.set(null);
    this.revision.set([]);
    this.informe.set([]);
    this.error.set(null);
  }

  protected async convertir(): Promise<void> {
    if (this.convirtiendo() || !this.elegidos().length) return;
    this.convirtiendo.set(true);
    this.error.set(null);
    try {
      const archivos = await Promise.all(this.elegidos().map(async (e) => ({ nombre: e.archivo.name, contenido: await base64De(e.archivo, e.archivo.name) })));
      // Los AUX (por el nombre) van directo; el resto se lee y, si alguno resulta
      // ser un AUX con otro nombre, vuelve marcado y también va por convertirAux.
      let aux = archivos.filter((a) => NOMBRE_AUX.test(a.nombre));
      const otros = archivos.filter((a) => !NOMBRE_AUX.test(a.nombre));
      this.revision.set([]);
      this.informe.set([]);
      if (otros.length) {
        const leido = await this.api.extraerComprobantes(otros);
        const auxPorContenido = new Set(leido.archivos.filter((a) => a.tipo === 'aux').map((a) => a.nombre));
        aux = [...aux, ...otros.filter((a) => auxPorContenido.has(a.nombre))];
        this.informe.set(leido.archivos.filter((a) => a.tipo !== 'aux'));
        this.revision.set(leido.comprobantes.map((c) => ({ ...c, avisos: validarComprobante(c) })));
        if (leido.comprobantes.length) {
          this.toast.info('Revisá lo leído', `${leido.comprobantes.length} ${leido.comprobantes.length === 1 ? 'comprobante leído' : 'comprobantes leídos'}: revisalos abajo antes de prepararlos.`);
        }
      }
      if (!aux.length) {
        this.resultadosAux = [];
        this.resultados.set(null);
        return;
      }
      const r = await this.api.convertirAux(aux);
      this.resultadosAux = r.resultados;
      this.resultados.set(r.resultados);
      this.cargados.set({});
      this.estadoXubio.set({});
      const ok = r.resultados.reduce((n, x) => n + Object.keys(x.zips).length, 0);
      if (ok) this.toast.ok('Listo', `${ok} ${ok === 1 ? 'archivo .zip listo' : 'archivos .zip listos'} para Xubio.`);
      const vacios = r.resultados.filter((x) => !x.error && !Object.keys(x.zips).length).length;
      if (vacios) {
        this.toast.info(
          'Nada para subir',
          vacios === 1 ? 'Un cliente no tiene nada para convertir: todo ARCA está en su Libro IVA.' : `${vacios} clientes no tienen nada para convertir: todo ARCA está en su Libro IVA.`,
        );
      }
    } catch (err) {
      this.error.set(mensajeDeError(err, 'No se pudieron convertir los AUX.'));
    } finally {
      this.convirtiendo.set(false);
    }
  }

  /** Los comprobantes revisados al paso 2, junto a lo convertido de los AUX. */
  protected async prepararComprobantes(): Promise<void> {
    const incluidos = this.revision().filter((c) => c.incluir);
    if (this.preparando() || !incluidos.length) return;
    this.preparando.set(true);
    this.error.set(null);
    try {
      const r = await this.api.prepararComprobantes(incluidos);
      this.resultados.set([...this.resultadosAux, ...r.resultados]);
      this.empresas.set({});
      this.cargados.set({});
      this.estadoXubio.set({});
      const ok = r.resultados.reduce((n, x) => n + Object.keys(x.zips).length, 0);
      if (ok) this.toast.ok('Listo', `${ok} ${ok === 1 ? 'archivo .zip listo' : 'archivos .zip listos'} para Xubio.`);
    } catch (err) {
      this.error.set(mensajeDeError(err, 'No se pudieron preparar los comprobantes.'));
    } finally {
      this.preparando.set(false);
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

const FORMATOS = /\.(xlsx|xls|csv|pdf|jpe?g|png)$/i;

/** Solo para la etiqueta: el servidor detecta la operación por el contenido. */
export function etiquetaDe(nombre: string): string {
  if (/\.pdf$/i.test(nombre)) return 'PDF';
  if (/\.(jpe?g|png)$/i.test(nombre)) return 'Imagen';
  if (/\.csv$/i.test(nombre)) return 'CSV';
  const n = nombre.toLowerCase();
  const ventas = /venta|emitid/.test(n);
  const compras = /compra|recibid/.test(n);
  return ventas && !compras ? 'Ventas' : compras && !ventas ? 'Compras' : 'Excel';
}

function normalizar(s: string): string {
  return s.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function capitalizar(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

/** El archivo en base64 (sin el prefijo «data:…;base64,»). */
function base64De(archivo: Blob, nombre: string): Promise<string> {
  return new Promise((ok, mal) => {
    const lector = new FileReader();
    lector.onload = () => ok(String(lector.result).split(',', 2)[1] ?? '');
    lector.onerror = () => mal(new Error(`No se pudo leer «${nombre}».`));
    lector.readAsDataURL(archivo);
  });
}
