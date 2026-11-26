import { Component, HostListener, OnDestroy, computed, inject, signal } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { descargarBlob } from '../../core/descargar';
import { ItemLeido, LecturaExcel, Verificacion, mensajeDeError } from '../../core/models';
import { ApiService } from '../../core/services/api.service';
import { Alerta } from '../../core/ui/alerta';
import { Ayuda } from '../../core/ui/ayuda';
import { Encabezado } from '../../core/ui/encabezado';
import { Extras, Factura } from './factura';
import { VistaArchivo } from './vista-archivo';
import { Verificaciones } from './verificaciones';
import { ToastService } from '../../core/ui/toast';
import {
  ACEPTA, CAMPOS_ENCABEZADO, CAMPOS_TOTALES, EmpresaEstudio, FORMATOS_A_EXCEL, FacturaDelEstudio, Operacion, aNumero, aTexto, adaptar, contar,
  cuitConGuiones, detectarEmpresa, empresaDeTrabajo, recordarEmpresaDeTrabajo, itemVacio, nivelDe, sinCae, sinDuda, sumaItems, usarSugerencia,
} from './logica';

/**
 * «Conversor De Facturas» (antes «Pasar a Excel»): cualquier archivo (foto,
 * captura, PDF, CSV, Excel) a .xlsx. Con la misma estructura que «Subir a Xubio»:
 *
 * - Izquierda, «1. Elegí los archivos»: se arrastran y se leen solos
 *   (POST /api/a-excel/leer → una `LecturaExcel` por archivo).
 * - Derecha, «2. Pasalos a Excel»: del archivo elegido, qué hay que mirar y el
 *   botón para generar el .xlsx (POST /api/a-excel/generar).
 * - Abajo, a todo el ancho: el archivo y lo leído para corregir. Cada cambio
 *   vuelve a pedir POST /api/a-excel/verificar.
 *
 * Ver docs/pasar-a-excel.md para cómo está armado y qué falta.
 */
@Component({
  selector: 'app-a-excel',
  imports: [Encabezado, Alerta, Ayuda, Factura, VistaArchivo, Verificaciones],
  styles: `
    :host { display: flex; flex-direction: column; gap: 14px; }
    .zona { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; padding: 28px 16px; border: 2px dashed #cbd5e1; border-radius: 12px; background: #f8fafc; text-align: center; transition: border-color .15s, background .15s; }
    .zona.encima { border-color: var(--color-acento); background: #eff6ff; }
    .zona.con-archivos { align-items: stretch; justify-content: space-between; padding: 12px; gap: 12px; }
    .fx-elegidos { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 6px; }
    .fx-elegidos li { display: flex; align-items: center; gap: 10px; padding: 8px 10px; border: 1px solid var(--color-borde); border-radius: 8px; background: #fff; cursor: pointer; }
    .fx-elegidos li.activo { border-color: var(--color-acento); background: #eff6ff; box-shadow: inset 3px 0 0 var(--color-acento); }
    .fx-agregar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 12px; }
    .fx-agregar-mas, .fx-agregar-acciones { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
    .op { display: inline-block; min-width: 76px; padding: 0 8px; border-radius: 4px; font-size: .74rem; font-weight: 800; text-transform: uppercase; text-align: center; }
    .op-factura { background: #e0f2fe; color: #075985; }
    .op-tabla { background: #fef3c7; color: #92400e; }
    .op-error { background: #fee2e2; color: #991b1b; }
    .fx-cab-der { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; padding-bottom: 14px; border-bottom: 1px solid var(--color-borde); }
    .fx-res-cab { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 8px; margin-top: 14px; font-size: 1rem; }
    .fx-res-cab .op { padding: 3px 8px; min-width: 0; }
    .fx-pasos { list-style: none; margin: 12px 0 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
    .fx-paso { display: grid; grid-template-columns: 30px minmax(0, 1fr); gap: 12px; }
    .fx-num { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 999px; background: #eff6ff; color: var(--color-acento); font-weight: 800; font-size: .9rem; }
    .fx-paso h3 { display: flex; align-items: center; gap: 4px; margin: 4px 0 8px; font-size: .98rem; font-weight: 700; text-transform: none; letter-spacing: normal; color: var(--color-texto, #0f172a); }
    .fx-cargando { display: flex; align-items: center; gap: 10px; padding: 40px 0; justify-content: center; color: var(--color-tenue); font-weight: 600; }
    .fx-subir { padding: 10px 18px; font-size: .95rem; }
    .fx-estado { display: flex; flex-wrap: wrap; gap: 6px; }
    .fx-estado span { padding: 4px 10px; border-radius: 999px; font-size: .82rem; font-weight: 700; }
    .fx-estado .e-error { background: #fef2f2; color: #991b1b; }
    .fx-estado .e-aviso { background: #fffbeb; color: #92400e; }
    .fx-estado .e-ok { background: #f0fdf4; color: #166534; }
    .fx-pie { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 10px; margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--color-borde); }
    .fx-link { border: 0; background: none; padding: 0; color: var(--color-tenue); font-size: .85rem; text-decoration: underline; cursor: pointer; }
    .fx-link:hover { color: var(--color-acento); }
    .ax-punto { width: 8px; height: 8px; border-radius: 50%; flex: none; background: #16a34a; }
    .ax-punto.aviso { background: #f59e0b; } .ax-punto.error { background: #dc2626; }
    .ax-grilla { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); gap: 16px; align-items: start; }
    .ax-grilla.sin-vista { grid-template-columns: minmax(0, 1fr); }
    @media (max-width: 900px) { .ax-grilla { grid-template-columns: minmax(0, 1fr); } }
    .ax-sub { margin: 16px 0 6px; font-size: .8rem; font-weight: 800; color: var(--color-tenue); text-transform: uppercase; letter-spacing: .03em; }
    .ax-campos { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 8px 12px; }
    .ax-campos label { display: flex; flex-direction: column; gap: 2px; font-size: .74rem; font-weight: 700; color: #64748b; }
    .ax-in { width: 100%; min-width: 0; padding: 4px 6px; border: 1px solid #cbd5e1; border-radius: 6px; font: inherit; font-size: .85rem; background: #fff; }
    .ax-in:focus { outline: 2px solid var(--color-acento); outline-offset: -1px; }
    .ax-in.num { text-align: right; font-variant-numeric: tabular-nums; }
    .ax-in.error { border-color: #dc2626; background: #fef2f2; }
    .ax-in.aviso { border-color: #f59e0b; background: #fffbeb; }
    .ax-in.ok { border-color: #86efac; }
    .ax-caja { border: 1px solid var(--color-borde); border-radius: 10px; overflow-x: auto; background: #fff; }
    .ax-tabla { width: 100%; border-collapse: collapse; font-size: .8rem; }
    .ax-tabla th { text-align: left; font-size: .72rem; font-weight: 700; color: #64748b; padding: 6px; background: #f8fafc; border-bottom: 1px solid #e2e8f0; white-space: nowrap; }
    .ax-tabla td { padding: 3px 5px; border-bottom: 1px solid #f1f5f9; }
    .ax-tabla tfoot td { font-weight: 700; padding: 6px; }
    .ax-texto { white-space: pre-wrap; font-family: ui-monospace, monospace; font-size: .75rem; background: #f8fafc; padding: 10px; border-radius: 8px; max-height: 320px; overflow: auto; }
    /* Para el estudio */
    .fx-estudio { margin-top: 10px; padding: 10px 14px; border: 1px solid #c7d2fe; border-radius: 12px; background: #f8f9ff; }
    .fx-estudio h3 { display: flex; align-items: center; gap: 6px; margin: 0 0 6px; font-size: .98rem; font-weight: 800; color: #312e81; }
    .fx-est-fila { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px 14px; }
    .fx-est-fila label { display: flex; flex-direction: column; gap: 3px; flex: 1 1 200px; font-size: .74rem; font-weight: 700; color: #64748b; }
    .fx-seg { display: inline-flex; border: 1px solid #c7d2fe; border-radius: 8px; overflow: hidden; }
    .fx-seg button { border: 0; background: #fff; padding: 6px 14px; font-weight: 700; font-size: .85rem; color: #475569; cursor: pointer; }
    .fx-seg button + button { border-left: 1px solid #c7d2fe; }
    .fx-seg button[aria-pressed="true"] { background: #4338ca; color: #fff; }
    .fx-seg button:disabled { cursor: not-allowed; opacity: .55; }
    .fx-detec { margin: 6px 0 0; padding: 7px 10px; border-radius: 8px; font-size: .84rem; line-height: 1.35; }
    .fx-detec.ok { background: #f0fdf4; color: #166534; }
    .fx-detec.aviso { background: #fffbeb; color: #92400e; }
    .fx-mas { margin-top: 8px; font-size: .84rem; } .fx-mas summary { cursor: pointer; font-weight: 700; color: #4338ca; }
    .fx-bloque { margin-top: 10px; padding-top: 10px; border-top: 1px dashed #c7d2fe; }
    .fx-bloque-cab { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; margin-bottom: 8px; }
    .fx-bloque-cab strong { font-size: .9rem; }
    .fx-chip { padding: 2px 9px; border-radius: 999px; font-size: .74rem; font-weight: 800; }
    .fx-chip.gris { background: #f1f5f9; color: #475569; }
    .fx-chip.pronto { background: #ede9fe; color: #5b21b6; }
    .fx-arca { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; font-size: .84rem; color: #475569; }
    .fx-arca ul { margin: 0; padding-left: 18px; }
    .fx-opciones { display: grid; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); gap: 8px; }
    .fx-opcion { display: flex; flex-direction: column; gap: 3px; padding: 10px 12px; border: 1.5px solid var(--color-borde); border-radius: 10px; background: #fff; text-align: left; cursor: pointer; font: inherit; }
    .fx-opcion[aria-pressed="true"] { border-color: #4338ca; background: #eef2ff; box-shadow: inset 0 0 0 1px #4338ca; }
    .fx-opcion:disabled { cursor: not-allowed; opacity: .55; }
    .fx-opcion strong { font-size: .88rem; color: #1e1b4b; }
    .fx-opcion span { font-size: .78rem; color: #64748b; line-height: 1.3; }
    .fx-xubio-pie { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-top: 10px; }
  `,
  template: `
    <app-encabezado
      titulo="Conversor De Facturas"
      bajada="Fotos, capturas, PDF, CSV o Excel viejo (.xls) a un Excel. Antes de armarlo te muestro lo que leí para que lo verifiques."
    />

    <!-- Izquierda: elegir los archivos. Derecha: el archivo elegido, paso a paso. Abajo: corregir lo leído -->
    <div class="fx-grilla">
      <section class="mf-panel" aria-labelledby="t-elegir" data-tour="ax-archivos">
        <div class="fx-cab">
          <h2 id="t-elegir" class="m-0 text-lg font-bold">1. Elegí los archivos<app-ayuda texto="Se leen apenas los soltás. Las fotos y capturas las lee la IA (Gemini) y, si no está disponible, el OCR; de los PDF digitales se lee su texto; de CSV y planillas, las celdas. Una captura también la podés pegar con Ctrl+V." /></h2>
          <p class="mf-sub m-0 mt-1">Facturas o tablas: fotos o capturas (JPG, PNG), PDF (digital o escaneado), CSV, XLS o XLSX. Hasta 20 por vez.</p>
        </div>

        <div class="fx-cuerpo">
          <div
            class="zona"
            [class.con-archivos]="lecturas().length"
            [class.encima]="encima()"
            (dragover)="$event.preventDefault(); encima.set(true)"
            (dragleave)="encima.set(false)"
            (drop)="soltar($event)"
          >
            @if (lecturas().length) {
              <ul class="fx-elegidos" aria-label="Archivos leídos">
                @for (l of lecturas(); track l.archivo; let i = $index) {
                  <li [class.activo]="i === indice()" (click)="indice.set(i)" [attr.aria-current]="i === indice() ? 'true' : null">
                    <span [class]="'op ' + claseTipo(l)">{{ nombreTipo(l) }}</span>
                    <span class="min-w-0 flex-1 text-left">
                      <strong class="block truncate" [title]="l.archivo">{{ l.archivo }}</strong>
                      <span class="mf-tenue block truncate text-sm">{{ resumen(l) }}</span>
                    </span>
                    <span class="ax-punto" [class.error]="l.error || errores(l)" [class.aviso]="!l.error && !errores(l) && avisos(l)" aria-hidden="true"></span>
                    <button type="button" class="mf-icono-btn" (click)="quitar(i); $event.stopPropagation()" [attr.aria-label]="'Quitar ' + l.archivo" [disabled]="leyendo()">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" class="h-3.5 w-3.5" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
                    </button>
                  </li>
                }
              </ul>
              <div class="fx-agregar">
                <span class="fx-agregar-mas">
                  @if (leyendo()) {
                    <span class="inline-flex items-center gap-2 text-sm font-semibold"><span class="ej-spinner" aria-hidden="true"></span> Leyendo…</span>
                  } @else {
                    <span class="mf-tenue text-sm">Arrastrá más acá o</span>
                    <label class="mf-btn mf-btn-suave mf-btn-chico cursor-pointer">
                      Agregar
                      <input type="file" multiple class="sr-only" [attr.accept]="acepta" (change)="elegir($event)" />
                    </label>
                  }
                </span>
                <span class="fx-agregar-acciones">
                  <button type="button" class="mf-btn mf-btn-suave" (click)="limpiar()" [disabled]="leyendo()">Sacar todos</button>
                </span>
              </div>
            } @else if (leyendo()) {
              <span class="inline-flex items-center gap-2 font-semibold"><span class="ej-spinner" aria-hidden="true"></span> Leyendo…</span>
              <span class="mf-tenue text-sm">Las fotos tardan unos segundos.</span>
            } @else {
              <span class="font-semibold">Arrastrá los archivos acá</span>
              <span class="mf-tenue text-sm">Fotos, capturas, PDF, CSV o planillas · una captura, pegala con Ctrl+V</span>
              <span class="mf-tenue text-sm">o</span>
              <label class="mf-btn mf-btn-suave cursor-pointer">
                Elegir archivos
                <input type="file" multiple class="sr-only" [attr.accept]="acepta" (change)="elegir($event)" />
              </label>
            }
          </div>
          @if (error(); as err) { <app-alerta class="mt-3" nivel="error" [mensajes]="[err]" [descartable]="true" (cerrado)="error.set(null)" /> }
        </div>
      </section>

      <section class="mf-panel" aria-labelledby="t-resultado">
        <div class="fx-cab-der">
          <div class="min-w-0 flex-1">
            <h2 id="t-resultado" class="m-0 text-lg font-bold">2. Pasalos a Excel<app-ayuda texto="Lo rojo seguro está mal (por ejemplo un CUIT que no pasa el dígito verificador o importes que no suman el total). Lo amarillo conviene compararlo con el archivo." /></h2>
            <p class="mf-sub m-0 mt-1">Revisá lo que leí y generá el Excel de cada archivo.</p>
          </div>
        </div>

        <div class="fx-cuerpo">
          @if (leyendo() && !actual()) {
            <div class="fx-cargando" aria-live="polite">
              <span class="inline-block h-5 w-5 shrink-0 animate-spin rounded-full border-2 border-slate-300 border-t-[var(--color-acento)]" aria-hidden="true"></span>
              Leyendo los archivos…
            </div>
          } @else if (actual(); as l) {
            <div class="fx-res-cab">
              <span [class]="'op ' + claseTipo(l)">{{ nombreTipo(l) }}</span>
              <strong class="min-w-0 flex-1 truncate" [title]="l.archivo">{{ l.archivo }}</strong>
            </div>
            @if (l.error) {
              <app-alerta class="mt-4" nivel="error" [mensajes]="[l.error]" />
            } @else {
              @if (l.tipo === 'comprobante') {
                <section class="fx-estudio" aria-labelledby="fx-estudio-t" data-tour="ax-estudio">
                  <h3 id="fx-estudio-t">Para el estudio<app-ayuda texto="Busca las CUIT de la factura entre los clientes cargados: si la empresa es el receptor es una compra; si es el emisor, una venta. Podés cambiar la empresa o la operación a mano." /></h3>
                  <div class="fx-est-fila">
                    <label>
                      Empresa del estudio
                      <select class="ax-in" (change)="elegirEmpresa($any($event.target).value)" [disabled]="!empresas().length">
                        <option value="" [selected]="!estudio()">{{ empresas().length ? '— Elegí la empresa —' : 'Sin clientes para elegir' }}</option>
                        @for (e of empresas(); track e.nombre) { <option [value]="e.nombre" [selected]="estudio()?.empresa === e.nombre">{{ e.nombre }}</option> }
                      </select>
                    </label>
                    <div class="fx-seg" role="group" aria-label="Operación">
                      <button type="button" [attr.aria-pressed]="estudio()?.operacion === 'Compras'" [disabled]="!estudio()" (click)="elegirOperacion('Compras')">Compra</button>
                      <button type="button" [attr.aria-pressed]="estudio()?.operacion === 'Ventas'" [disabled]="!estudio()" (click)="elegirOperacion('Ventas')">Venta</button>
                    </div>
                  </div>

                  @if (estudio(); as f) {
                    @if (f.origen === 'receptor' || f.origen === 'emisor') {
                      <p class="fx-detec ok">✓ Detectada: <b>{{ f.empresa }}</b> es el {{ f.origen }} de la factura, así que es {{ f.operacion === 'Compras' ? 'una compra' : 'una venta' }}.</p>
                    } @else if (f.origen === 'trabajo') {
                      <p class="fx-detec aviso">La factura no nombra a ningún cliente del estudio: la tomé para <b>{{ f.empresa }}</b>, la empresa con la que venís trabajando, como compra. Cambiala arriba si no es así.</p>
                    } @else {
                      <p class="fx-detec aviso">{{ avisoManual() }}</p>
                    }
                  } @else {
                    <p class="fx-detec aviso">
                      @if (!empresas().length) { No se pudieron ver los clientes del estudio (cargalos o desbloqueá «Clientes»). }
                      @else { Ninguna CUIT de la factura es de un cliente del estudio. Elegí la empresa arriba si corresponde. }
                    </p>
                  }

                  <details class="fx-mas" data-tour="ax-xubio">
                  <summary>ARCA y Xubio <span class="fx-chip pronto">Próximamente</span></summary>
                  <div class="fx-bloque" data-tour="ax-arca">
                    <div class="fx-bloque-cab"><strong>En ARCA</strong><span class="fx-chip gris">Sin consultar</span><span class="fx-chip pronto">Próximamente</span></div>
                    <div class="fx-arca">
                      <span>Se va a buscar en «Mis Comprobantes» {{ estudio() ? 'de ' + estudio()!.empresa : 'de la empresa' }} por:</span>
                      <ul><li>CUIT {{ estudio()?.operacion === 'Ventas' ? 'del receptor' : 'del emisor' }}, punto de venta y número</li><li>fecha y total</li></ul>
                      <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" disabled title="Todavía no está conectado">Buscar en ARCA</button>
                    </div>
                  </div>

                  <div class="fx-bloque">
                    <div class="fx-bloque-cab"><strong>En Xubio</strong><span class="fx-chip pronto">Próximamente</span></div>
                    <div class="fx-opciones" role="group" aria-label="Qué hacer en Xubio">
                      @for (o of opcionesXubio(); track o.id) {
                        <button type="button" class="fx-opcion" [attr.aria-pressed]="opcionXubio() === o.id" [disabled]="!estudio()" (click)="elegirXubio(o.id)">
                          <strong>{{ o.titulo }}</strong><span>{{ o.texto }}</span>
                        </button>
                      }
                    </div>
                    <div class="fx-xubio-pie">
                      <button type="button" class="mf-btn" disabled title="Todavía no está conectado">{{ textoXubio() }}</button>
                      <span class="mf-tenue">Por ahora: generá el Excel abajo y cargalo a mano en Xubio.</span>
                    </div>
                  </div>
                  </details>
                </section>
              }
              <ol class="fx-pasos">
                <li class="fx-paso">
                  <span class="fx-num" aria-hidden="true">1</span>
                  <div class="min-w-0">
                    <h3>Revisá lo que leí</h3>
                    <p class="mf-sub m-0 mb-2">
                      Leído {{ l.metodo === 'ia' ? 'con IA (Gemini) de la imagen' : l.metodo === 'ocr' ? 'con OCR (de la imagen)' : l.metodo === 'pdf' ? 'del texto del PDF' : 'de la planilla' }}
                      @if (verificando()) { · <span class="ej-spinner" aria-hidden="true"></span> verificando… }
                    </p>
                    <div class="fx-estado">
                      @if (errores(l)) { <span class="e-error">{{ errores(l) }} {{ errores(l) === 1 ? 'error' : 'errores' }}</span> }
                      @if (avisos(l)) { <span class="e-aviso">{{ avisos(l) }} para mirar</span> }
                      @if (bien().length) { <span class="e-ok">{{ bien().length }} bien</span> }
                    </div>
                    <button type="button" class="fx-link mt-2" (click)="irARevision()">Ver y corregir abajo ↓</button>
                  </div>
                </li>
                <li class="fx-paso">
                  <span class="fx-num" aria-hidden="true">2</span>
                  <div class="min-w-0">
                    <h3>Generá el Excel</h3>
                    <button type="button" class="mf-btn fx-subir inline-flex items-center gap-2" (click)="generar()" [disabled]="generando() || verificando()" [attr.aria-busy]="generando()" data-tour="ax-generar">
                      @if (generando()) {
                        <span class="inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden="true"></span>
                        Armando…
                      } @else {
                        ⬇ {{ errores(l) ? 'Generar el Excel igual' : 'Generar Excel' }}
                      }
                    </button>
                    @if (errores(l)) { <p class="m-0 mt-2 text-sm font-semibold text-red-700">Quedan {{ errores(l) }} {{ errores(l) === 1 ? 'error' : 'errores' }} sin corregir.</p> }
                  </div>
                </li>
              </ol>
            }
            <div class="fx-pie">
              <span class="mf-tenue text-sm">{{ lecturas().length }} {{ lecturas().length === 1 ? 'archivo leído' : 'archivos leídos' }}</span>
              <button type="button" class="fx-link ml-auto" (click)="quitar(indice())" [disabled]="leyendo()">Sacar este archivo</button>
            </div>
          } @else {
            <div class="fx-vacio">
              <strong>Acá aparece lo que leí</strong>
              <span class="text-sm">Elegí los archivos en el paso 1: se leen solos.</span>
            </div>
          }
        </div>
      </section>
    </div>

    @if (actual(); as l) {
      @if (!l.error) {
        <section class="mf-panel" id="ax-revision" aria-labelledby="ax-titulo" data-tour="ax-verificar">
          <div class="ax-grilla" [class.sin-vista]="!vista()">
            @if (vista(); as v) {
              <app-vista-archivo [url]="v.url" [tipo]="v.tipo" [nombre]="l.archivo" />
            }
            <div class="min-w-0">
              <h2 id="ax-titulo" class="m-0 text-lg font-bold">
                Verificá lo que leí
                <app-ayuda texto="Lo rojo seguro está mal (por ejemplo un CUIT que no pasa el dígito verificador o importes que no suman el total). Lo amarillo conviene compararlo con el archivo. Si las cuentas dan el valor correcto, aparece «Usar»." />
              </h2>
              <p class="mf-sub mt-1">{{ l.archivo }}</p>
              @if (l.avisos.length) { <app-alerta class="mb-2 block" nivel="aviso" [mensajes]="l.avisos" /> }

              <app-verificaciones [lista]="verificaciones()" (usar)="usar($event)" (elegir)="elegirLectura($event[0], $event[1])" />

              @if (l.tipo === 'comprobante') {
                <app-factura
                  [lectura]="l"
                  [estudio]="estudio()"
                  [sesion]="sesion()"
                  [nivel]="nivelFn"
                  [ocupado]="generando() || verificando()"
                  (encabezado)="cambiarEncabezado($event[0], $event[1])"
                  (total)="cambiarTotal($event[0], $event[1])"
                  (item)="cambiarItem($event[0], $event[1], $event[2])"
                  (agregar)="agregarItem()"
                  (sacar)="sacarItem($event)"
                  (guardar)="generar($event)"
                  (eliminar)="quitar(indice())"
                  (cancelar)="volverArriba()"
                />
              } @else {
                @for (h of l.hojas; track h.nombre) {
                  <h3 class="ax-sub">{{ h.nombre }} · {{ h.filas.length }} filas</h3>
                  <div class="ax-caja">
                    <table class="ax-tabla">
                      <tbody>
                        @for (f of h.filas.slice(0, 60); track $index) {
                          <tr>@for (c of f; track $index) { <td>{{ c }}</td> }</tr>
                        }
                      </tbody>
                    </table>
                  </div>
                  @if (h.filas.length > 60) { <p class="mf-sub mt-1">Se muestran 60 de {{ h.filas.length }} filas: el Excel las lleva todas.</p> }
                }
              }

              @if (l.texto) {
                <details class="mt-4">
                  <summary class="cursor-pointer text-sm font-semibold">Ver el texto que se leyó</summary>
                  <div class="ax-texto mt-2">{{ textoSinCae(l.texto) }}</div>
                </details>
              }
            </div>
          </div>
        </section>
      }
    }
  `,
})
export class PasarAExcel implements OnDestroy {
  private readonly api = inject(ApiService);
  private readonly toast = inject(ToastService);
  private readonly sanitizer = inject(DomSanitizer);

  protected readonly acepta = ACEPTA;
  protected readonly camposEncabezado = CAMPOS_ENCABEZADO;
  protected readonly camposTotales = CAMPOS_TOTALES;

  protected readonly lecturas = signal<LecturaExcel[]>([]);
  protected readonly indice = signal(0);
  protected readonly leyendo = signal(false);
  protected readonly verificando = signal(false);
  protected readonly generando = signal(false);
  protected readonly encima = signal(false);
  protected readonly error = signal<string | null>(null);
  /** archivo → vista previa (object URL de la imagen o del PDF). */
  private readonly vistas = signal<Record<string, { url: string; seguro: SafeResourceUrl; tipo: 'imagen' | 'pdf' }>>({});
  private temporizador: ReturnType<typeof setTimeout> | null = null;

  protected readonly actual = computed(() => this.lecturas()[this.indice()] ?? null);
  protected readonly vista = computed(() => {
    const l = this.actual();
    return l ? (this.vistas()[l.archivo] ?? null) : null;
  });
  // El CAE no se muestra: sus controles tampoco.
  private readonly verificaciones = computed(() => (this.actual()?.verificaciones ?? []).filter((v) => v.campo !== 'cae'));
  protected readonly aMirar = computed(() => this.verificaciones().filter((v) => v.nivel !== 'ok'));
  protected readonly bien = computed(() => this.verificaciones().filter((v) => v.nivel === 'ok'));
  protected readonly suma = computed(() => sumaItems(this.actual()?.items ?? []));

  protected readonly errores = (l: LecturaExcel) => contar(l, 'error');
  protected readonly avisos = (l: LecturaExcel) => contar(l, 'aviso');
  protected readonly texto = aTexto;

  // --- Para el estudio (solo pantalla: ARCA y Xubio todavía no están conectados) ---
  /** Los clientes del estudio con sus CUIT; vacío si Clientes está bloqueado o no hay. */
  protected readonly empresas = signal<EmpresaEstudio[]>([]);
  /** Lo que se eligió a mano por archivo (manda sobre lo detectado). */
  private readonly elegido = signal<Record<string, { empresa: string; operacion: Operacion | null }>>({});
  private readonly xubioElegido = signal<Record<string, string>>({});
  protected readonly cuit = cuitConGuiones;
  /** El usuario de Xubio guardado: el estudio que está trabajando. */
  protected readonly sesion = signal('');
  protected readonly nivelFn = (campo: string) => this.nivel(campo);
  protected readonly textoSinCae = sinCae;

  /** La factura vista desde la empresa del estudio (detectada o elegida). */
  protected readonly estudio = computed<FacturaDelEstudio | null>(() => {
    const l = this.actual();
    if (!l || l.tipo !== 'comprobante') return null;
    const detectada = detectarEmpresa(l.encabezado, this.empresas());
    const manual = this.elegido()[l.archivo];
    if (!manual) {
      if (detectada) return detectada;
      const trabajo = this.empresas().find((e) => e.nombre === empresaDeTrabajo());
      return trabajo ? adaptar(l.encabezado, trabajo.nombre, 'Compras', 'trabajo') : null;
    }
    if (!manual.empresa) return null;
    const mismaEmpresa = detectada?.empresa === manual.empresa;
    const operacion = manual.operacion ?? (mismaEmpresa ? detectada!.operacion : 'Compras');
    const origen = mismaEmpresa && operacion === detectada!.operacion ? detectada!.origen : 'manual';
    return adaptar(l.encabezado, manual.empresa, operacion, origen);
  });

  /** Por qué lo elegido a mano no coincide con la factura. */
  protected readonly avisoManual = computed(() => {
    const l = this.actual();
    const f = this.estudio();
    if (!l || !f) return '';
    const detectada = detectarEmpresa(l.encabezado, this.empresas().filter((e) => e.nombre === f.empresa));
    if (!detectada) return `Elegida a mano: ninguna CUIT de la factura es de ${f.empresa}. Revisá que sea la empresa correcta.`;
    const es = detectada.operacion === 'Compras' ? 'una compra' : 'una venta';
    return `Ojo: ${f.empresa} es el ${detectada.origen} de la factura, así que es ${es}. La marcaste como ${f.operacion === 'Compras' ? 'compra' : 'venta'}.`;
  });

  protected readonly opcionesXubio = computed(() => {
    const compra = this.estudio()?.operacion !== 'Ventas';
    return [
      { id: 'cargar', titulo: compra ? 'Cargar como compra' : 'Cargar como venta', texto: compra ? 'Factura de proveedor con sus importes y alícuotas.' : 'Factura de venta con sus importes y alícuotas.' },
      { id: 'detalle', titulo: 'Con el detalle', texto: 'Cada ítem de la factura como una línea en Xubio.' },
      { id: 'arca', titulo: 'Desde Mis Comprobantes', texto: 'Importarla con el archivo de ARCA, como en «Subir a Xubio».' },
    ];
  });
  protected readonly opcionXubio = computed(() => {
    const l = this.actual();
    return l ? (this.xubioElegido()[l.archivo] ?? 'cargar') : 'cargar';
  });
  protected readonly textoXubio = computed(() => {
    const f = this.estudio();
    const o = this.opcionesXubio().find((x) => x.id === this.opcionXubio());
    return f && o ? `${o.titulo} en Xubio · ${f.empresa}` : 'Elegí la empresa para cargarla en Xubio';
  });

  constructor() {
    this.api.ajustes().then((a) => this.sesion.set(a.xubio?.usuario ?? ''), () => undefined);
    // Si Clientes está bloqueado, el panel avisa y se elige a mano después.
    this.api.clientes().then(
      (cs) => this.empresas.set(cs.map((c) => ({ nombre: c.nombre, cuits: [c.arca.cuit_representado, c.arca.cuit_login].filter(Boolean) }))),
      () => this.empresas.set([]),
    );
  }

  protected elegirEmpresa(nombre: string): void {
    const l = this.actual();
    if (l) this.elegido.update((m) => ({ ...m, [l.archivo]: { empresa: nombre, operacion: null } }));
    recordarEmpresaDeTrabajo(nombre);
  }

  protected elegirOperacion(operacion: Operacion): void {
    const l = this.actual();
    const f = this.estudio();
    if (l && f) this.elegido.update((m) => ({ ...m, [l.archivo]: { empresa: f.empresa, operacion } }));
  }

  protected elegirXubio(id: string): void {
    const l = this.actual();
    if (l) this.xubioElegido.update((m) => ({ ...m, [l.archivo]: id }));
  }

  /** Ctrl+V con una captura (o una imagen copiada) en cualquier lugar de la pantalla. */
  @HostListener('document:paste', ['$event'])
  protected pegar(e: ClipboardEvent): void {
    const destino = e.target as HTMLElement | null;
    // Pegar texto en un campo de la revisión sigue siendo pegar texto.
    if (destino?.closest('input, textarea, [contenteditable]')) return;
    const imagenes = Array.from(e.clipboardData?.files ?? []).filter((f) => f.type.startsWith('image/'));
    if (!imagenes.length) return;
    e.preventDefault();
    const hora = new Date().toTimeString().slice(0, 8).replace(/:/g, '');
    const archivos = imagenes.map((f, i) => {
      const ext = f.type === 'image/jpeg' ? 'jpg' : (f.type.split('/')[1] ?? 'png');
      return new File([f], `Captura ${hora}${imagenes.length > 1 ? `-${i + 1}` : ''}.${ext}`, { type: f.type });
    });
    void this.leer(archivos);
  }

  ngOnDestroy(): void {
    Object.values(this.vistas()).forEach((v) => URL.revokeObjectURL(v.url));
  }

  protected nivel(campo: string): string {
    return nivelDe(this.actual()?.verificaciones ?? [], campo) ?? '';
  }

  protected formatear(v: string | number): string {
    return typeof v === 'number' ? aTexto(v) : v;
  }

  /** La etiqueta de color de cada archivo (como Ventas / Compras en «Subir a Xubio»). */
  protected nombreTipo(l: LecturaExcel): string {
    return l.error ? 'No se leyó' : l.tipo === 'comprobante' ? 'Factura' : 'Tabla';
  }

  protected claseTipo(l: LecturaExcel): string {
    return l.error ? 'op-error' : l.tipo === 'comprobante' ? 'op-factura' : 'op-tabla';
  }

  /** Debajo del nombre: cómo quedó la verificación. */
  protected resumen(l: LecturaExcel): string {
    if (l.error) return 'No se pudo leer';
    const e = this.errores(l);
    const a = this.avisos(l);
    if (e) return `${e} ${e === 1 ? 'error' : 'errores'}${a ? ` · ${a} para mirar` : ''}`;
    if (a) return `${a} para mirar`;
    return 'Todo verificado';
  }

  protected volverArriba(): void {
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  protected irARevision(): void {
    document.getElementById('ax-revision')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  protected quitar(i: number): void {
    const l = this.lecturas()[i];
    if (!l) return;
    const v = this.vistas()[l.archivo];
    if (v) {
      URL.revokeObjectURL(v.url);
      const { [l.archivo]: _, ...resto } = this.vistas();
      this.vistas.set(resto);
    }
    this.lecturas.update((ls) => ls.filter((_, j) => j !== i));
    const n = this.lecturas().length;
    if (this.indice() >= n) this.indice.set(Math.max(0, n - 1));
  }

  protected limpiar(): void {
    Object.values(this.vistas()).forEach((v) => URL.revokeObjectURL(v.url));
    this.vistas.set({});
    this.lecturas.set([]);
    this.indice.set(0);
    this.error.set(null);
  }

  // --- Subir ------------------------------------------------------------------------

  protected soltar(e: DragEvent): void {
    e.preventDefault();
    this.encima.set(false);
    void this.leer(Array.from(e.dataTransfer?.files ?? []));
  }

  protected elegir(e: Event): void {
    const input = e.target as HTMLInputElement;
    void this.leer(Array.from(input.files ?? []));
    input.value = '';
  }

  private async leer(archivos: File[]): Promise<void> {
    this.error.set(null);
    const validos = archivos.filter((a) => FORMATOS_A_EXCEL.test(a.name)).slice(0, 20);
    const fuera = archivos.length - validos.length;
    if (fuera) this.toast.aviso(`${fuera} ${fuera === 1 ? 'archivo no se puede' : 'archivos no se pueden'} leer`, 'Solo fotos, PDF, CSV y Excel.');
    if (!validos.length) return;
    this.leyendo.set(true);
    try {
      const subidos = await Promise.all(validos.map(async (a) => ({ nombre: a.name, contenido: await base64De(a) })));
      const { lecturas } = await this.api.leerAExcel(subidos);
      const vistas = { ...this.vistas() };
      for (const a of validos) {
        const tipo = /\.pdf$/i.test(a.name) ? 'pdf' : /\.(jpe?g|png|webp|bmp)$/i.test(a.name) ? 'imagen' : null;
        if (!tipo) continue;
        if (vistas[a.name]) URL.revokeObjectURL(vistas[a.name].url);
        const url = URL.createObjectURL(a);
        // El PDF va en un iframe: Angular pide marcar la URL (es un blob: de esta misma página).
        vistas[a.name] = { url, seguro: this.sanitizer.bypassSecurityTrustResourceUrl(url), tipo };
      }
      this.vistas.set(vistas);
      // Los nuevos reemplazan a los del mismo nombre; el primero nuevo queda a la vista.
      const nombres = new Set(lecturas.map((l) => l.archivo));
      const resto = this.lecturas().filter((l) => !nombres.has(l.archivo));
      this.lecturas.set([...resto, ...lecturas]);
      this.indice.set(resto.length);
    } catch (err) {
      this.error.set(mensajeDeError(err, 'No se pudieron leer los archivos.'));
    } finally {
      this.leyendo.set(false);
    }
  }

  // --- Corregir -----------------------------------------------------------------------

  private reemplazar(nueva: LecturaExcel): void {
    const i = this.indice();
    this.lecturas.update((ls) => ls.map((l, j) => (j === i ? nueva : l)));
    this.reverificar();
  }

  /** Un rato después del último cambio, la verificación otra vez en el backend. */
  private reverificar(): void {
    if (this.temporizador) clearTimeout(this.temporizador);
    this.temporizador = setTimeout(async () => {
      const i = this.indice();
      const l = this.lecturas()[i];
      if (!l) return;
      this.verificando.set(true);
      try {
        const { verificaciones } = await this.api.verificarAExcel(l);
        this.lecturas.update((ls) => ls.map((x, j) => (j === i ? { ...x, verificaciones } : x)));
      } catch (err) {
        this.toast.error('No se pudo verificar', mensajeDeError(err, ''));
      } finally {
        this.verificando.set(false);
      }
    }, 350);
  }

  protected usar(v: Verificacion): void {
    const l = this.actual();
    if (l) this.reemplazar(usarSugerencia(l, v));
  }

  protected cambiarEncabezado(campo: string, valor: string): void {
    const l = this.actual();
    if (l) this.reemplazar(sinDuda({ ...l, encabezado: { ...l.encabezado, [campo]: valor.trim() } }, campo));
  }

  /** Entre lo que dieron las lecturas, el valor que eligió el usuario. */
  protected elegirLectura(campo: string, valor: string): void {
    if (CAMPOS_TOTALES.some((c) => c.id === campo)) this.cambiarTotal(campo, valor);
    else this.cambiarEncabezado(campo, valor);
  }

  protected cambiarTotal(campo: string, valor: string): void {
    const l = this.actual();
    if (l) this.reemplazar(sinDuda({ ...l, totales: { ...l.totales, [campo]: aNumero(valor) } }, campo));
  }

  protected cambiarItem(i: number, campo: keyof ItemLeido, valor: string): void {
    const l = this.actual();
    if (!l) return;
    const nuevo = campo === 'codigo' || campo === 'descripcion' ? valor : aNumero(valor);
    this.reemplazar({ ...l, items: l.items.map((x, j) => (j === i ? { ...x, [campo]: nuevo } : x)) });
  }

  protected agregarItem(): void {
    const l = this.actual();
    if (l) this.reemplazar({ ...l, items: [...l.items, itemVacio()] });
  }

  protected sacarItem(i: number): void {
    const l = this.actual();
    if (l) this.reemplazar({ ...l, items: l.items.filter((_, j) => j !== i) });
  }

  // --- Generar ------------------------------------------------------------------------

  protected async generar(extras?: Extras): Promise<void> {
    const l = this.actual();
    if (!l) return;
    this.generando.set(true);
    try {
      const est = this.estudio();
      const r = await this.api.generarAExcel(l, est ? { operacion: est.operacion, empresa: est.empresa, extras } : {});
      const bytes = Uint8Array.from(atob(r.contenido), (c) => c.charCodeAt(0));
      descargarBlob(r.nombre, new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' }));
      this.toast.ok('Excel listo', r.nombre);
    } catch (err) {
      this.toast.error('No se pudo armar el Excel', mensajeDeError(err, ''));
    } finally {
      this.generando.set(false);
    }
  }
}

/** El archivo en base64 (sin el prefijo «data:…;base64,»). */
function base64De(archivo: Blob): Promise<string> {
  return new Promise((ok, mal) => {
    const lector = new FileReader();
    lector.onload = () => ok(String(lector.result).split(',', 2)[1] ?? '');
    lector.onerror = () => mal(new Error('No se pudo leer el archivo.'));
    lector.readAsDataURL(archivo);
  });
}
