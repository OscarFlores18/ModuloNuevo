import { Component, HostListener, OnDestroy, computed, inject, signal } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { descargarBlob } from '../../core/descargar';
import { AlicuotaIva, ItemLeido, LecturaExcel, NivelVerificacion, TributoLeido, Verificacion, mensajeDeError } from '../../core/models';
import { ApiService } from '../../core/services/api.service';
import { Alerta } from '../../core/ui/alerta';
import { Ayuda } from '../../core/ui/ayuda';
import { Encabezado } from '../../core/ui/encabezado';
import { VistaArchivo } from './vista-archivo';
import { corta } from './verificaciones';
import { ToastService } from '../../core/ui/toast';
import {
  ACEPTA, ALICUOTAS_IVA, CAMPOS_ENCABEZADO, CAMPOS_TOTALES, EmpresaEstudio, FORMATOS_A_EXCEL, FacturaDelEstudio, Operacion, aNumero, aTexto, adaptar, contar,
  cuitConGuiones, detectarEmpresa, empresaDeTrabajo, recordarEmpresaDeTrabajo, itemVacio, nivelDe, sinCae, sinDuda, sumaItems, usarSugerencia,
  alicuotaLibre, conDesglose, conTributos, grupoTributo, nombreAlicuota, productosSugeridos, revisarDesglose,
} from './logica';

/** Los totales que salen de las filas de «Otros tributos». */
const TRIBUTOS = ['impuestos_internos', 'percepcion_iibb', 'otros_tributos'];
const COLUMNA_TRIBUTO: Record<string, string> = {
  impuestos_internos: 'Impuestos internos',
  percepcion_iibb: 'Percepción IIBB Bs. As.',
  otros_tributos: 'Otros tributos',
};
const redondear2 = (n: number) => Math.round(n * 100) / 100;

/** Lo que no puede quedar vacío en el encabezado y los importes. */
const OBLIGATORIOS = new Set(['emisor', 'emisor_cuit', 'tipo', 'numero', 'fecha', 'total']);

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
  imports: [Encabezado, Alerta, Ayuda, VistaArchivo],
  styles: `
    :host {
      --ax-texto: #0f172a; --ax-tenue: #64748b; --ax-borde: #e2e8f0; --ax-borde-in: #cbd5e1; --ax-fondo: #f8fafc;
      --ax-acento: #1d4ed8; --ax-error: #b91c1c; --ax-aviso: #b45309; --ax-ok: #15803d;
      display: flex; flex-direction: column; gap: 14px; flex: 1 0 auto; color: var(--ax-texto);
    }
    /* Sin archivos */
    .ax-inicio { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 6px; width: 100%; max-width: 640px; margin: auto; padding: 40px 24px; background: #fff; border: 1px dashed var(--ax-borde-in); border-radius: 8px; text-align: center; cursor: pointer; }
    .ax-inicio:hover, .ax-inicio.encima { border-color: var(--ax-acento); }
    .ax-inicio-i { width: 28px; height: 28px; margin-bottom: 4px; color: var(--ax-tenue); }
    .ax-inicio-t { font-size: 1rem; font-weight: 600; }
    .ax-inicio-err { width: 100%; max-width: 640px; margin: 0 auto auto; }

    /* Arriba: archivos y el elegido */
    .ax-top { display: grid; grid-template-columns: 300px minmax(0, 1fr); gap: 14px; align-items: start; }
    @media (max-width: 860px) { .ax-top { grid-template-columns: minmax(0, 1fr); } }
    .ax-tarjeta { min-width: 0; padding: 14px 16px; background: #fff; border: 1px solid var(--ax-borde); border-radius: 8px; }
    .ax-tarjeta.encima { border-color: var(--ax-acento); }
    .ax-cab, .ax-barra { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
    .ax-cab { margin-bottom: 10px; }
    .ax-cab h2, .ax-titulo { margin: 0; font-size: .95rem; font-weight: 600; }
    .ax-titulo { display: flex; flex-direction: column; min-width: 0; }
    .ax-meta { font-size: .8rem; font-weight: 400; color: var(--ax-tenue); }
    .ax-lista { margin: 0; padding: 0; list-style: none; border: 1px solid var(--ax-borde); border-radius: 6px; }
    .ax-lista li { display: flex; align-items: center; gap: 8px; padding: 0 6px 0 10px; }
    .ax-lista li + li { border-top: 1px solid var(--ax-borde); }
    .ax-lista li.activo { background: var(--ax-fondo); box-shadow: inset 2px 0 0 var(--ax-acento); }
    .ax-fila { flex: 1; min-width: 0; padding: 7px 0; border: 0; background: none; font: inherit; text-align: left; cursor: pointer; }
    .ax-nombre { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: .875rem; font-weight: 600; }
    .ax-x { flex: none; width: 28px; height: 28px; border: 0; border-radius: 4px; background: none; color: var(--ax-tenue); font-size: 1.1rem; line-height: 1; cursor: pointer; }
    .ax-x:hover { background: var(--ax-fondo); color: var(--ax-error); }
    .ax-leyendo { display: flex; align-items: center; gap: 6px; margin: 8px 0 0; }
    .ax-link { border: 0; background: none; padding: 0; color: var(--ax-acento); font: inherit; font-size: .85rem; font-weight: 600; cursor: pointer; }
    .ax-link:hover { text-decoration: underline; }
    .ax-limpiar { display: block; margin: 10px 0 0 auto; color: var(--ax-tenue); }
    .ax-pend { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: baseline; margin: 10px 0 0; font-size: .85rem; color: var(--ax-error); }
    .ax-descargar { flex: none; display: inline-flex; align-items: center; gap: 8px; padding: 8px 16px; }
    .ax-inicio:focus-visible, .ax-fila:focus-visible, .ax-x:focus-visible, .ax-link:focus-visible, .ax-quitar:focus-visible, .ax-mas:focus-visible { outline: 2px solid var(--ax-acento); outline-offset: 2px; }
    .ax-punto { width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--ax-borde-in); }
    .ax-punto.aviso { background: #f59e0b; } .ax-punto.error { background: #dc2626; }
    .ax-empresa { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--ax-borde); font-size: .85rem; }
    .ax-empresa label { display: flex; align-items: center; gap: 8px; font-weight: 600; }
    .ax-empresa select { width: auto; min-width: 220px; }
    .ax-empresa p { flex-basis: 100%; margin: 0; color: var(--ax-tenue); }
    .ax-empresa p.aviso { color: var(--ax-aviso); }

    /* Abajo: el archivo y lo leído */
    .ax-grilla { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); gap: 16px; align-items: start; }
    .ax-grilla.sin-vista { grid-template-columns: minmax(0, 1fr); }
    @media (max-width: 900px) { .ax-grilla { grid-template-columns: minmax(0, 1fr); } }
    .ax-ver-cab { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 14px; margin-bottom: 10px; }
    .ax-ver-cab h2 { margin: 0; font-size: 1.05rem; font-weight: 600; }
    .ax-op { flex: 1 1 320px; display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; padding: 8px 12px; border: 1px solid var(--ax-borde); border-radius: 8px; background: var(--ax-fondo); font-size: .88rem; }
    .ax-op.aviso { border-color: #fcd34d; background: #fffbeb; }
    .ax-op-tipo { font-weight: 700; }
    .ax-op-frase { flex: 1 1 240px; color: #334155; }
    .ax-ver { list-style: none; margin: 0 0 4px; padding: 0; display: flex; flex-direction: column; gap: 4px; }
    .ax-ver li { display: flex; flex-wrap: wrap; gap: 6px 8px; align-items: center; padding: 6px 10px; border-left: 3px solid var(--ax-borde-in); background: var(--ax-fondo); border-radius: 0 6px 6px 0; font-size: .84rem; line-height: 1.35; }
    .ax-ver li.error { border-left-color: #dc2626; } .ax-ver li.aviso { border-left-color: #f59e0b; } .ax-ver li.ok { border-left-color: #22c55e; color: var(--ax-ok); }
    .ax-ver li p { margin: 0; flex: 1 1 200px; }

    /* Cada bloque: encabezado, importes, IVA, tributos, total, detalle */
    .ax-bloque { margin-top: 14px; border: 1px solid var(--ax-borde); border-radius: 8px; background: #fff; overflow: hidden; }
    .ax-bloque > header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 10px; padding: 8px 12px; border-bottom: 1px solid var(--ax-borde); }
    .ax-bloque > header h3 { margin: 0; font-size: .875rem; font-weight: 600; }
    .ax-bloque > header span { font-size: .78rem; color: var(--ax-tenue); }
    .ax-campos { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 10px 12px; padding: 10px 12px 12px; }
    .ax-campos label { display: flex; flex-direction: column; gap: 3px; font-size: .78rem; color: var(--ax-tenue); }
    .ax-partes { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
    .ax-parte { min-width: 0; margin: 0; padding: 10px 12px 12px; border: 0; display: grid; grid-template-columns: minmax(0, 2fr) minmax(0, 1fr); gap: 8px 10px; }
    .ax-parte + .ax-parte { border-left: 1px solid #f1f5f9; }
    .ax-parte legend { float: left; grid-column: 1 / -1; padding: 0; font-size: .8rem; font-weight: 600; color: var(--ax-texto); }
    .ax-parte label { display: flex; flex-direction: column; gap: 3px; font-size: .78rem; color: var(--ax-tenue); }
    .ax-papel { margin-left: 6px; font-weight: 400; color: var(--ax-tenue); }
    .ax-comp { border-top: 1px solid #f1f5f9; }
    @media (max-width: 640px) { .ax-partes { grid-template-columns: minmax(0, 1fr); } .ax-parte + .ax-parte { border-left: 0; border-top: 1px solid #f1f5f9; } }
    .ax-acciones { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
    .ax-xubio { flex: none; padding: 8px 14px; }
    .ax-xubio-res { margin-top: 10px; padding: 8px 10px; border-left: 3px solid #22c55e; background: var(--ax-fondo); border-radius: 0 6px 6px 0; font-size: .84rem; color: var(--ax-ok); }
    .ax-xubio-res.aviso { border-left-color: #f59e0b; color: var(--ax-aviso); }
    .ax-xubio-res p { margin: 0; } .ax-xubio-res p + p { margin-top: 3px; }
    .ax-renglon { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 5px 12px; font-size: .86rem; }
    .ax-renglon + .ax-renglon { border-top: 1px solid #f1f5f9; }
    .ax-renglon.ax-nuevo-grupo { border-top: 1px solid var(--ax-borde); }
    .ax-renglon .num { font-variant-numeric: tabular-nums; }
    .ax-renglon .ax-in { flex: 0 1 180px; }
    .ax-fila-imp { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(90px, 1fr) minmax(90px, 1fr) 30px; gap: 2px 8px; align-items: center; padding: 5px 12px; }
    .ax-fila-imp + .ax-fila-imp { border-top: 1px solid #f1f5f9; }
    .ax-fila-imp .num { text-align: right; font-variant-numeric: tabular-nums; }
    .ax-cols { padding-bottom: 0; font-size: .75rem; color: var(--ax-tenue); }
    .ax-nota { grid-column: 1 / -1; margin: 0; font-size: .76rem; line-height: 1.3; color: var(--ax-tenue); }
    .ax-nota.error { color: var(--ax-error); } .ax-nota.aviso { color: var(--ax-aviso); }
    .ax-suma { border-top: 1px solid var(--ax-borde) !important; background: var(--ax-fondo); font-size: .86rem; font-weight: 600; }
    .ax-suma .num { padding-right: 7px; }
    .ax-concepto { display: flex; gap: 6px; min-width: 0; }
    .ax-concepto .ax-cod { flex: 0 0 52px; text-align: center; }
    @media (max-width: 560px) { .ax-fila-imp { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) 30px; } .ax-fila-imp > :first-child { grid-column: 1 / -1; } .ax-cols { display: none; } }
    .ax-vacio { margin: 0; padding: 10px 12px; font-size: .84rem; color: var(--ax-tenue); }
    .ax-pie { padding: 8px 12px; border-top: 1px solid var(--ax-borde); }
    .ax-mas { padding: 4px 10px; border: 1px solid var(--ax-borde-in); border-radius: 6px; background: #fff; color: var(--ax-texto); font: inherit; font-size: .82rem; font-weight: 600; cursor: pointer; }
    .ax-mas:hover:not(:disabled) { background: var(--ax-fondo); }
    .ax-mas:disabled { opacity: .5; cursor: not-allowed; }
    .ax-quitar { display: inline-grid; place-items: center; width: 28px; height: 28px; border: 0; border-radius: 6px; background: transparent; color: #94a3b8; cursor: pointer; }
    .ax-quitar:hover { background: #fef2f2; color: var(--ax-error); }
    .ax-quitar svg { width: 14px; height: 14px; }
    .ax-total { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 12px; border-top: 1px solid var(--ax-borde); background: var(--ax-fondo); font-weight: 600; }
    .ax-total .ax-in { flex: 0 1 180px; font-weight: 600; }
    .ax-cuadre-msj { margin: 0; padding: 6px 12px 8px; font-size: .8rem; color: var(--ax-tenue); background: var(--ax-fondo); }
    .ax-cuadre-msj.ok { color: var(--ax-ok); } .ax-cuadre-msj.error { color: var(--ax-error); }

    /* Campos */
    .ax-in { width: 100%; min-width: 0; padding: 4px 7px; border: 1px solid var(--ax-borde-in); border-radius: 6px; font: inherit; font-size: .86rem; color: var(--ax-texto); background: #fff; }
    .ax-in:focus { outline: 2px solid var(--ax-acento); outline-offset: -1px; }
    .ax-in.num { text-align: right; font-variant-numeric: tabular-nums; }
    .ax-in.error { border-color: #dc2626; }
    .ax-in.aviso { border-color: #f59e0b; }

    /* Detalle y hojas */
    .ax-caja { overflow-x: auto; }
    .ax-tabla { width: 100%; border-collapse: collapse; font-size: .86rem; }
    .ax-tabla th { text-align: left; font-size: .75rem; font-weight: 500; color: var(--ax-tenue); padding: 6px; border-bottom: 1px solid var(--ax-borde); }
    .ax-tabla th.num, .ax-tabla td.num { text-align: right; font-variant-numeric: tabular-nums; }
    .ax-tabla td { padding: 3px 4px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }
    .ax-tabla tfoot td { padding: 8px 6px; border-bottom: 0; border-top: 1px solid var(--ax-borde); background: var(--ax-fondo); font-weight: 600; }
    .ax-mas-filas { margin: 0; padding: 6px 12px; font-size: .78rem; color: var(--ax-tenue); }
    .ax-texto-leido { margin-top: 14px; font-size: .84rem; }
    .ax-texto-leido summary { cursor: pointer; color: var(--ax-tenue); }
    .ax-nota-lectura { margin: 6px 0 0; color: var(--ax-tenue); }
    .ax-texto { margin-top: 8px; white-space: pre-wrap; font: .75rem ui-monospace, monospace; background: var(--ax-fondo); padding: 10px; border-radius: 6px; max-height: 320px; overflow: auto; }
  `,
  template: `
    <app-encabezado
      titulo="Conversor De Facturas"
      bajada="Subí fotos, PDF, CSV o Excel: los leo, los revisás y descargás el Excel."
    />

    <input #entrada type="file" multiple class="sr-only" tabindex="-1" aria-hidden="true" [attr.accept]="acepta" (change)="elegir($event)" />

    @if (!lecturas().length) {
      <div
        class="ax-inicio"
        [class.encima]="encima()"
        role="button"
        tabindex="0"
        aria-describedby="ax-inicio-sub"
        [attr.aria-busy]="leyendo()"
        [attr.aria-disabled]="leyendo()"
        data-tour="ax-archivos"
        (click)="abrirSelector(entrada)"
        (keydown.enter)="$event.preventDefault(); abrirSelector(entrada)"
        (keydown.space)="$event.preventDefault(); abrirSelector(entrada)"
        (dragover)="$event.preventDefault(); encima.set(true)"
        (dragleave)="encima.set(false)"
        (drop)="soltar($event)"
      >
        @if (leyendo()) {
          <span class="ej-spinner" aria-hidden="true"></span>
          <strong class="ax-inicio-t">Leyendo los archivos…</strong>
          <span class="ax-meta">Las fotos tardan unos segundos.</span>
        } @else {
          <svg class="ax-inicio-i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 15V4M7 9l5-5 5 5M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3" /></svg>
          <strong class="ax-inicio-t">Arrastrá los archivos o hacé clic para elegir</strong>
          <span id="ax-inicio-sub" class="ax-meta">PDF, JPG, PNG, CSV, XLS o XLSX, hasta 20. También podés pegar una captura con Ctrl + V.</span>
        }
      </div>
      @if (error(); as err) { <app-alerta class="ax-inicio-err" nivel="error" [mensajes]="[err]" [descartable]="true" (cerrado)="error.set(null)" /> }
    } @else {
      <div class="ax-top">
        <section
          class="ax-tarjeta"
          [class.encima]="encima()"
          aria-labelledby="t-elegir"
          data-tour="ax-archivos"
          (dragover)="$event.preventDefault(); encima.set(true)"
          (dragleave)="encima.set(false)"
          (drop)="soltar($event)"
        >
          <div class="ax-cab">
            <h2 id="t-elegir">Archivos <span class="ax-meta">({{ lecturas().length }})</span></h2>
            <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="abrirSelector(entrada)" [disabled]="leyendo()">+ Agregar</button>
          </div>
          <ul class="ax-lista" aria-label="Archivos leídos">
            @for (l of lecturas(); track l.archivo; let i = $index) {
              <li [class.activo]="i === indice()">
                <span class="ax-punto" [class.error]="l.error || errores(l)" [class.aviso]="!l.error && !errores(l) && avisos(l)" aria-hidden="true"></span>
                <button type="button" class="ax-fila" (click)="indice.set(i)" [attr.aria-current]="i === indice() ? 'true' : null">
                  <span class="ax-nombre" [title]="l.archivo">{{ l.archivo }}</span>
                  <span class="ax-meta">{{ nombreTipo(l) }}@if (pendientes(l); as p) { · {{ p }} para revisar }</span>
                </button>
                <button type="button" class="ax-x" (click)="quitar(i)" [attr.aria-label]="'Quitar ' + l.archivo" [disabled]="leyendo()">×</button>
              </li>
            }
          </ul>
          @if (leyendo()) { <p class="ax-meta ax-leyendo" aria-live="polite"><span class="ej-spinner" aria-hidden="true"></span> Leyendo…</p> }
          @if (lecturas().length > 1) {
            <button type="button" class="ax-link ax-limpiar" (click)="limpiar()" [disabled]="leyendo()">Quitar todos</button>
          }
          @if (error(); as err) { <app-alerta class="mt-3" nivel="error" [mensajes]="[err]" [descartable]="true" (cerrado)="error.set(null)" /> }
        </section>

        <section class="ax-tarjeta" aria-labelledby="t-resultado">
          @if (actual(); as l) {
            <div class="ax-barra">
              <h2 id="t-resultado" class="ax-titulo">
                <span class="ax-nombre" [title]="l.archivo">{{ l.archivo }}</span>
                <span class="ax-meta">{{ nombreTipo(l) }}</span>
              </h2>
              @if (!l.error) {
                <div class="ax-acciones">
                @if (l.tipo === 'comprobante') {
                  <button type="button" class="mf-btn mf-btn-suave ax-xubio" (click)="prepararXubio()" [disabled]="!estudio() || preparando() || verificando()" [attr.aria-busy]="preparando()"
                    [attr.title]="estudio() ? 'Arma el archivo para «Importar desde Mis Comprobantes (ARCA)» de Xubio' : 'Elegí primero la empresa'">
                    {{ preparando() ? 'Preparando…' : 'Preparar para Xubio' }}
                  </button>
                }
                <button type="button" class="mf-btn ax-descargar" (click)="generar()" [disabled]="generando() || verificando()" [attr.aria-busy]="generando()" data-tour="ax-generar">
                  @if (generando()) {
                    <span class="inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden="true"></span>
                    Armando…
                  } @else {
                    Descargar Excel
                  }
                </button>
                </div>
              }
            </div>
            @if (!l.error && paraXubio(); as x) {
              <div class="ax-xubio-res" [class.aviso]="!x.listo || x.avisos.length" role="status">
                @if (x.listo) { <p>✓ Listo para Xubio: se descargó «{{ x.nombre }}». Falta que el bot lo cargue (todavía no está conectado).</p> }
                @for (a of x.avisos; track $index) { <p>{{ a }}</p> }
              </div>
            }
            @if (l.error) {
              <app-alerta class="mt-3" nivel="error" [mensajes]="[l.error]" />
            } @else {
              @if (pendientes(l); as p) {
                <p class="ax-pend" role="status">
                  {{ p }} {{ p === 1 ? 'campo para revisar' : 'campos para revisar' }}
                  <button type="button" class="ax-link" (click)="irARevision()">Ir a corregir ↓</button>
                </p>
              }
              @if (l.tipo === 'comprobante') {
                <div class="ax-empresa" data-tour="ax-estudio">
                  <label>
                    Empresa
                    <select class="ax-in" (change)="elegirEmpresa($any($event.target).value)" [disabled]="!empresas().length">
                      <option value="" [selected]="!estudio()">{{ empresas().length ? 'Elegí la empresa' : 'Sin clientes para elegir' }}</option>
                      @for (e of empresas(); track e.nombre) { <option [value]="e.nombre" [selected]="estudio()?.empresa === e.nombre">{{ e.nombre }}</option> }
                    </select>
                  </label>
                  @if (!empresas().length) { <p class="aviso">No se pudieron ver los clientes del estudio.</p> }
                </div>
              }
            }
          }
        </section>
      </div>
    }

    @if (actual(); as l) {
      @if (!l.error) {
        <section class="mf-panel" id="ax-revision" aria-labelledby="ax-titulo" data-tour="ax-verificar">
          <div class="ax-grilla" [class.sin-vista]="!vista()">
            @if (vista(); as v) {
              <app-vista-archivo [url]="v.url" [tipo]="v.tipo" [nombre]="l.archivo" />
            }
            <div class="min-w-0">
              <div class="ax-ver-cab">
                <h2 id="ax-titulo">
                  Verificá lo que leí
                  <app-ayuda texto="Solo te aviso lo que no cierra (las cuentas, un CUIT mal) o lo que falta. Si la cuenta da el valor correcto, aparece «Usar»." />
                </h2>
              @if (l.tipo === 'comprobante') {
                <div class="ax-op" [class.aviso]="!estudio() || estudio()!.origen === 'manual' || estudio()!.origen === 'trabajo'" role="status">
                  @if (estudio(); as f) {
                    <span class="ax-op-tipo">{{ f.operacion === 'Compras' ? 'Compra' : 'Venta' }}</span>
                    <span class="ax-op-frase">{{ fraseOperacion() }}</span>
                    <button type="button" class="ax-link" (click)="elegirOperacion(f.operacion === 'Compras' ? 'Ventas' : 'Compras')">Es {{ f.operacion === 'Compras' ? 'venta' : 'compra' }}</button>
                  } @else {
                    <span class="ax-op-tipo">¿Compra o venta?</span>
                    <span class="ax-op-frase">{{ empresas().length ? 'Ninguna CUIT de la factura es de un cliente: elegí la empresa arriba y te digo cuál es.' : 'Sin la lista de clientes no puedo saberlo.' }}</span>
                  }
                </div>
              }
              </div>
              @if (l.avisos.length && sinDatos(l)) { <app-alerta class="mb-2 block" nivel="aviso" [mensajes]="l.avisos" /> }

              <ul class="ax-ver" aria-live="polite">
                @for (v of aMirar(); track $index) {
                  @let c = corta(v);
                  <li [class]="v.nivel">
                    <p [title]="v.mensaje">{{ c.titulo || c.mensaje }}@if (v.nivel === 'error' && c.datos?.length) { · {{ c.datos!.join(' · ') }} }</p>
                    @for (o of c.opciones ?? []; track o) {
                      <button type="button" class="ax-mas" (click)="elegirLectura(v.campo, o)" [title]="'Usar «' + o + '»'">{{ o }}</button>
                    }
                    @if (v.sugerencia !== null && v.sugerencia !== undefined) {
                      <button type="button" class="mf-btn mf-btn-chico" (click)="usar(v)">Usar {{ formatear(v.sugerencia) }}</button>
                    }
                  </li>
                }
                @if (!aMirar().length) {
                  <li class="ok" [title]="resumenBien()"><p>✓ Todo cuadra</p></li>
                }
              </ul>

              @if (l.tipo === 'comprobante') {
                <section class="ax-bloque" aria-labelledby="ax-enc-t">
                  <header><h3 id="ax-enc-t">Encabezado</h3></header>
                  <div class="ax-partes">
                    @for (rol of roles; track rol) {
                      <fieldset class="ax-parte">
                        <legend>{{ rol === 'emisor' ? 'Emisor' : 'Receptor' }}@if (papel(rol); as p) { <span class="ax-papel">{{ p }}</span> }</legend>
                        <label>Razón social
                          <input class="ax-in" [class]="nivel(rol)" [attr.title]="explicar(rol, estadoCampo(rol, l.encabezado[rol]))" [value]="l.encabezado[rol] ?? ''" (change)="cambiarEncabezado(rol, $any($event.target).value)" />
                        </label>
                        <label>CUIT
                          <input class="ax-in" [class]="nivel(rol + '_cuit')" [attr.title]="explicar(rol + '_cuit', estadoCampo(rol + '_cuit', l.encabezado[rol + '_cuit']))" [value]="l.encabezado[rol + '_cuit'] ?? ''" (change)="cambiarEncabezado(rol + '_cuit', $any($event.target).value)" />
                        </label>
                      </fieldset>
                    }
                  </div>
                  <div class="ax-campos ax-comp">
                    @for (c of camposComprobante; track c.id) {
                      <label>{{ c.etiqueta }}
                        <input class="ax-in" [class]="nivel(c.id)" [attr.title]="explicar(c.id, estadoCampo(c.id, l.encabezado[c.id]))" [placeholder]="c.ayuda ?? ''" [value]="l.encabezado[c.id] ?? ''" (change)="cambiarEncabezado(c.id, $any($event.target).value)" />
                      </label>
                    }
                  </div>
                </section>

                <section class="ax-bloque" aria-labelledby="ax-importes-t">
                  <header><h3 id="ax-importes-t">Importes sin IVA</h3></header>
                  @for (c of camposImportes(); track c.id) {
                    <label class="ax-renglon">
                      <span>{{ c.etiqueta }}</span>
                      <input class="ax-in num" [class]="nivel(c.id)" [attr.title]="explicar(c.id, estadoCampo(c.id, l.totales[c.id]))" [value]="texto(l.totales[c.id])" (change)="cambiarTotal(c.id, $any($event.target).value)" />
                    </label>
                  }
                </section>

                <section class="ax-bloque" aria-labelledby="ax-iva-titulo">
                  <header><h3 id="ax-iva-titulo">IVA</h3></header>
                  @if (desglose().length) {
                    <div class="ax-fila-imp ax-cols" aria-hidden="true">
                      <span>Alícuota</span><span class="num">Base imponible</span><span class="num">Importe</span><span></span>
                    </div>
                  }
                  @for (f of desglose(); track $index; let i = $index) {
                    <div class="ax-fila-imp" role="group" [attr.aria-label]="nombreAlicuota(f.alicuota)">
                      <select class="ax-in" [attr.aria-label]="'Alícuota de la fila ' + (i + 1)" (change)="cambiarAlicuota(i, $any($event.target).value)">
                        @if (f.alicuota === null) { <option value="" selected>Elegí…</option> }
                        @for (a of alicuotasDe(f.alicuota); track a) {
                          <option [value]="a" [selected]="a === f.alicuota">{{ nombreAlicuota(a) }}</option>
                        }
                      </select>
                      <input class="ax-in num" inputmode="decimal" [attr.aria-label]="'Base imponible ' + nombreAlicuota(f.alicuota)" [value]="texto(f.base)" (change)="cambiarFilaIva(i, 'base', $any($event.target).value)" />
                      <input class="ax-in num" inputmode="decimal" [class]="f.estado === 'ok' ? '' : f.estado" [attr.aria-label]="'Importe ' + nombreAlicuota(f.alicuota)" [attr.aria-invalid]="f.estado === 'error' ? 'true' : null" [attr.title]="f.motivo" [value]="texto(f.importe)" (change)="cambiarFilaIva(i, 'importe', $any($event.target).value)" />
                      <button type="button" class="ax-quitar" (click)="sacarAlicuota(i)" [attr.aria-label]="'Quitar ' + nombreAlicuota(f.alicuota)">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
                      </button>
                      @if (f.estado !== 'ok') { <p class="ax-nota" [class]="f.estado">{{ f.motivo }}</p> }
                    </div>
                  } @empty {
                    <p class="ax-vacio">Sin IVA discriminado (factura B/C o tique a consumidor final).</p>
                  }
                  <div class="ax-fila-imp ax-suma">
                    <span>Total IVA</span>
                    <span class="num">{{ desglose().length ? texto(sumaBases()) : '' }}</span>
                    <span class="num">{{ texto(l.totales['iva']) || '—' }}</span>
                    <span></span>
                  </div>
                  <div class="ax-pie"><button type="button" class="ax-mas" (click)="agregarAlicuota()" [disabled]="!puedeAgregarAlicuota()">+ Alícuota</button></div>
                </section>

                <section class="ax-bloque" aria-labelledby="ax-trib-titulo">
                  <header><h3 id="ax-trib-titulo">Otros tributos</h3><span>Percepciones, impuestos internos, tasas</span></header>
                  @if ((l.tributos ?? []).length) {
                    <div class="ax-fila-imp ax-cols" aria-hidden="true">
                      <span>Concepto</span><span class="num">Base imponible</span><span class="num">Importe</span><span></span>
                    </div>
                  }
                  @for (t of l.tributos ?? []; track $index; let i = $index) {
                    @let estado = estadoCampo('tributo_' + i, t.importe);
                    <div class="ax-fila-imp" role="group" [attr.aria-label]="t.descripcion || 'Tributo ' + (i + 1)">
                      <div class="ax-concepto">
                        <input class="ax-in ax-cod" [attr.aria-label]="'Código del tributo ' + (i + 1)" placeholder="Cód." [value]="t.codigo" (change)="cambiarTributo(i, 'codigo', $any($event.target).value)" />
                        <input class="ax-in" [attr.aria-label]="'Descripción del tributo ' + (i + 1)" placeholder="Ej.: Percepción IIBB Bs. As." [value]="t.descripcion" (change)="cambiarTributo(i, 'descripcion', $any($event.target).value)" />
                      </div>
                      <input class="ax-in num" inputmode="decimal" [attr.aria-label]="'Base imponible de ' + (t.descripcion || 'el tributo ' + (i + 1))" [value]="texto(t.base)" (change)="cambiarTributo(i, 'base', $any($event.target).value)" />
                      <input class="ax-in num" inputmode="decimal" [class]="estado === 'ok' ? '' : (estado ?? '')" [attr.aria-label]="'Importe de ' + (t.descripcion || 'el tributo ' + (i + 1))" [attr.title]="explicar('tributo_' + i, estado)" [value]="texto(t.importe)" (change)="cambiarTributo(i, 'importe', $any($event.target).value)" />
                      <button type="button" class="ax-quitar" (click)="sacarTributo(i)" [attr.aria-label]="'Quitar ' + (t.descripcion || 'el tributo ' + (i + 1))">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
                      </button>
                      <p class="ax-nota">Va a «{{ columnaTributo(t) }}»@if (porcentaje(t); as p) { · {{ p }} de la base }</p>
                    </div>
                  } @empty {
                    <p class="ax-vacio">Sin otros tributos.</p>
                  }
                  @if ((l.tributos ?? []).length || totalTributos()) {
                    <div class="ax-fila-imp ax-suma">
                      <span>Total otros tributos</span>
                      <span></span>
                      <span class="num">{{ texto(totalTributos()) || '—' }}</span>
                      <span></span>
                    </div>
                  }
                  <div class="ax-pie"><button type="button" class="ax-mas" (click)="agregarTributo()">+ Tributo</button></div>
                </section>

                <section class="ax-bloque" aria-labelledby="ax-cuadre-t">
                  <header><h3 id="ax-cuadre-t">Total</h3></header>
                  @for (p of cuadre().partes; track $index; let i = $index) {
                    <div class="ax-renglon" [class.ax-nuevo-grupo]="i > 0 && cuadre().partes[i - 1].grupo !== p.grupo">
                      <span>{{ p.nombre }}</span><span class="num">{{ texto(p.valor) }}</span>
                    </div>
                  }
                  <label class="ax-total">Total de la factura
                    <input class="ax-in num" [class]="nivel('total')" [attr.title]="explicar('total', estadoCampo('total', l.totales['total']))" [value]="texto(l.totales['total'])" (change)="cambiarTotal('total', $any($event.target).value)" />
                  </label>
                  <p class="ax-cuadre-msj" [class]="cuadre().estado" role="status">
                    @if (cuadre().estado === 'ok') { ✓ La suma da el total. }
                    @else if (cuadre().estado === 'error') { La suma da {{ texto(cuadre().suma) }}: difiere en {{ texto(cuadre().diferencia) }}. }
                    @else { Falta el total. }
                  </p>
                </section>

                <section class="ax-bloque" aria-labelledby="ax-det-t">
                  <header><h3 id="ax-det-t">Detalle</h3><span>{{ l.items.length }} {{ l.items.length === 1 ? 'ítem' : 'ítems' }}</span></header>
                  <div class="ax-caja">
                    <table class="ax-tabla" aria-labelledby="ax-det-t">
                      <thead><tr><th>Código</th><th>Descripción</th><th>Producto</th><th class="num">Cant.</th><th class="num">Precio</th><th class="num">IVA %</th><th class="num">Importe</th><th><span class="sr-only">Quitar</span></th></tr></thead>
                      <tbody>
                        @for (x of l.items; track $index; let i = $index) {
                          <tr>
                            <td style="width: 80px"><input class="ax-in" [value]="x.codigo" (change)="cambiarItem(i, 'codigo', $any($event.target).value)" aria-label="Código" /></td>
                            <td><input class="ax-in" [class.aviso]="!!x.leido && x.leido !== x.descripcion" [attr.title]="x.leido && x.leido !== x.descripcion ? 'Corregido solo: decía «' + x.leido + '»' : null" [value]="x.descripcion" (change)="cambiarItem(i, 'descripcion', $any($event.target).value)" aria-label="Descripción" /></td>
                            <td style="width: 140px"><input class="ax-in" [value]="x.producto ?? ''" [attr.list]="'ax-productos'" (change)="cambiarItem(i, 'producto', $any($event.target).value)" aria-label="Producto" /></td>
                            <td style="width: 64px"><input class="ax-in num" [value]="texto(x.cantidad)" (change)="cambiarItem(i, 'cantidad', $any($event.target).value)" aria-label="Cantidad" /></td>
                            <td style="width: 100px"><input class="ax-in num" [value]="texto(x.precio)" (change)="cambiarItem(i, 'precio', $any($event.target).value)" aria-label="Precio" /></td>
                            <td style="width: 60px"><input class="ax-in num" [value]="x.alicuota ?? ''" (change)="cambiarItem(i, 'alicuota', $any($event.target).value)" aria-label="IVA %" /></td>
                            <td style="width: 110px"><input class="ax-in num" [class.aviso]="importeCorregido(x)" [attr.title]="importeCorregido(x) ? 'Corregido solo: se leyó ' + texto(x.importe_leido) : null" [value]="texto(x.importe)" (change)="cambiarItem(i, 'importe', $any($event.target).value)" aria-label="Importe" /></td>
                            <td style="width: 34px">
                              <button type="button" class="ax-quitar" (click)="sacarItem(i)" aria-label="Sacar el ítem">
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
                              </button>
                            </td>
                          </tr>
                        }
                      </tbody>
                      <tfoot>
                        <tr>
                          <td colspan="3"><button type="button" class="ax-mas" (click)="agregarItem()">+ Ítem</button></td>
                          <td colspan="3" class="num">Suma del detalle</td>
                          <td class="num">{{ texto(suma()) }}</td><td></td>
                        </tr>
                      </tfoot>
                    </table>
                  </div>
                  <datalist id="ax-productos">@for (p of productos; track p) { <option [value]="p"></option> }</datalist>
                </section>
              } @else {
                @for (h of l.hojas; track h.nombre) {
                  <section class="ax-bloque">
                    <header><h3>{{ h.nombre }}</h3><span>{{ h.filas.length }} filas</span></header>
                    <div class="ax-caja">
                      <table class="ax-tabla">
                        <tbody>
                          @for (f of h.filas.slice(0, 60); track $index) {
                            <tr>@for (c of f; track $index) { <td>{{ c }}</td> }</tr>
                          }
                        </tbody>
                      </table>
                    </div>
                    @if (h.filas.length > 60) { <p class="ax-mas-filas">Se muestran 60 de {{ h.filas.length }} filas; el Excel las lleva todas.</p> }
                  </section>
                }
              }

              @if (l.texto || (l.avisos.length && !sinDatos(l))) {
                <details class="ax-texto-leido">
                  <summary>Cómo se leyó{{ nombreMotor(l) ? " · " + nombreMotor(l) : "" }}</summary>
                  @if (!sinDatos(l)) { @for (a of l.avisos; track $index) { <p class="ax-nota-lectura">{{ a }}</p> } }
                  @if (l.texto) { <div class="ax-texto">{{ textoSinCae(l.texto) }}</div> }
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
  protected readonly roles = ['emisor', 'receptor'] as const;
  /** Tipo, número y fecha (emisor y receptor van aparte, cada uno en su lado). */
  protected readonly camposComprobante = CAMPOS_ENCABEZADO.filter((c) => !/^(emisor|receptor)/.test(c.id));
  protected readonly preparando = signal(false);
  /** archivo → lo que dio «Preparar para Xubio» (se borra al cambiar algo de la factura). */
  private readonly xubio = signal<Record<string, { listo: boolean; avisos: string[]; nombre: string }>>({});
  protected readonly paraXubio = computed(() => {
    const l = this.actual();
    return l ? (this.xubio()[l.archivo] ?? null) : null;
  });
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
  /** El desglose de IVA con sus cuentas, al instante (sin esperar al backend). */
  protected readonly desglose = computed(() => {
    const l = this.actual();
    return l ? revisarDesglose({ ...l, iva_alicuotas: l.iva_alicuotas ?? [] }) : [];
  });
  protected readonly sumaBases = computed(() => Math.round(this.desglose().reduce((s, f) => s + (f.base ?? 0), 0) * 100) / 100);
  /** Todas las filas de tributos (internos, percepciones y el resto). */
  protected readonly sumaTributos = computed(() => Math.round((this.actual()?.tributos ?? []).reduce((s, f) => s + (f.importe ?? 0), 0) * 100) / 100);
  /** Lo que dice «Otros tributos»: la suma de las filas o, sin filas, los totales leídos. */
  protected readonly totalTributos = computed(() => {
    const l = this.actual();
    if (!l) return null;
    if ((l.tributos ?? []).length) return this.sumaTributos();
    const t = TRIBUTOS.map((c) => l.totales[c]).filter((v): v is number => typeof v === 'number');
    return t.length ? redondear2(t.reduce((s, v) => s + v, 0)) : null;
  });
  /** Los importes sueltos: los tributos se cargan abajo, renglón por renglón, y el total va en el cuadre. */
  protected readonly camposImportes = computed(() => {
    const conFilas = (this.actual()?.tributos ?? []).length > 0;
    return CAMPOS_TOTALES.filter((c) => c.id !== 'total' && !(conFilas && TRIBUTOS.includes(c.id)));
  });
  /** Neto + no gravado + exento + IVA + otros tributos contra el total de la factura. */
  protected readonly cuadre = computed(() => {
    const l = this.actual();
    const tot = l?.totales ?? {};
    const num = (v: unknown) => (typeof v === 'number' ? v : null);
    // Cada importe por separado, en el orden de la factura: bases, IVA de cada alícuota y cada tributo.
    const iva = this.desglose().length
      ? this.desglose().map((d) => ({ nombre: nombreAlicuota(d.alicuota), valor: d.importe, grupo: 'iva' }))
      : [{ nombre: 'IVA', valor: num(tot['iva']), grupo: 'iva' }];
    const filas = l?.tributos ?? [];
    const tributos = filas.length
      ? filas.map((t, i) => ({ nombre: t.descripcion || `Tributo ${i + 1}`, valor: t.importe, grupo: 'trib' }))
      : TRIBUTOS.map((c) => ({ nombre: COLUMNA_TRIBUTO[c], valor: num(tot[c]), grupo: 'trib' }));
    const partes = [
      { nombre: 'Neto gravado', valor: num(tot['neto_gravado']), grupo: 'base' },
      { nombre: 'No gravado', valor: num(tot['no_gravado']), grupo: 'base' },
      { nombre: 'Exento', valor: num(tot['exento']), grupo: 'base' },
      ...iva,
      ...tributos,
    ].filter((p) => p.valor);
    const suma = redondear2(partes.reduce((s, p) => s + (p.valor ?? 0), 0));
    const total = num(tot['total']);
    const diferencia = total === null ? null : redondear2(Math.abs(total - suma));
    const estado: NivelVerificacion = diferencia === null ? 'aviso' : diferencia <= 0.05 ? 'ok' : 'error';
    return { partes, suma, diferencia, estado };
  });
  protected readonly puedeAgregarAlicuota = computed(() => alicuotaLibre(this.actual()?.iva_alicuotas ?? []) !== null);
  protected readonly nombreAlicuota = nombreAlicuota;

  protected readonly errores = (l: LecturaExcel) => contar(l, 'error');
  protected readonly avisos = (l: LecturaExcel) => contar(l, 'aviso');
  /** No se sacó nada útil: ahí sí importan los avisos de la lectura (sin clave de Gemini, sin OCR…). */
  protected sinDatos(l: LecturaExcel): boolean {
    if (l.tipo === 'tabla') return !l.hojas.some((h) => h.filas.length);
    return !l.items.length && !Object.values(l.totales).some((v) => v !== null && v !== undefined);
  }
  protected nombreMotor(l: LecturaExcel): string {
    const m = (l.motor ?? '').toLowerCase();
    if (l.metodo === 'ia' || m.includes('gemini')) return 'IA (Gemini)';
    if (m.includes('paddle')) return 'OCR local (PaddleOCR)';
    if (l.metodo === 'ocr') return 'OCR local (Tesseract)';
    return l.metodo === 'pdf' ? 'texto del PDF' : '';
  }
  protected readonly texto = aTexto;

  // --- Para el estudio: de qué empresa es y si es compra o venta ---
  /** Los clientes del estudio con sus CUIT; vacío si Clientes está bloqueado o no hay. */
  protected readonly empresas = signal<EmpresaEstudio[]>([]);
  /** Lo que se eligió a mano por archivo (manda sobre lo detectado). */
  private readonly elegido = signal<Record<string, { empresa: string; operacion: Operacion | null }>>({});
  protected readonly cuit = cuitConGuiones;
  protected readonly nivelFn = (campo: string) => this.nivel(campo);
  /** Los productos para elegir en el detalle (los de la empresa del estudio). */
  protected get productos(): string[] {
    const f = this.estudio();
    return productosSugeridos(f?.empresa ?? '', f?.operacion ?? 'Compras');
  }
  protected readonly textoSinCae = sinCae;
  protected readonly corta = corta;

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

  /** Compra o venta, dicho en una frase: quién es quién y de dónde se sacó. */
  protected readonly fraseOperacion = computed(() => {
    const f = this.estudio();
    if (!f) return '';
    const otro = f.contraparte.nombre || (f.operacion === 'Compras' ? 'el emisor' : 'consumidor final');
    switch (f.origen) {
      case 'receptor': return `${f.empresa} recibió la factura: le compró a ${otro}.`;
      case 'emisor': return `${f.empresa} emitió la factura: le vendió a ${otro}.`;
      case 'trabajo': return `La factura no nombra a ningún cliente: la tomé como compra de ${f.empresa}, la empresa con la que venís trabajando.`;
      default: return this.avisoManual() || `${f.empresa}, elegida a mano.`;
    }
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

  constructor() {
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

  /** Qué es cada parte para la empresa del estudio. */
  protected papel(rol: string): string {
    const f = this.estudio();
    if (!f) return '';
    const esLaEmpresa = (rol === 'receptor') === (f.operacion === 'Compras');
    return esLaEmpresa ? f.empresa : f.operacion === 'Compras' ? 'proveedor' : 'cliente';
  }

  private olvidarXubio(archivo: string): void {
    if (!this.xubio()[archivo]) return;
    this.xubio.update((m) => {
      const resto = { ...m };
      delete resto[archivo];
      return resto;
    });
  }

  /** Arma el .zip para «Importar desde Mis Comprobantes (ARCA)» y lo descarga. No abre Xubio. */
  protected async prepararXubio(): Promise<void> {
    const l = this.actual();
    const f = this.estudio();
    if (!l || !f) return;
    this.preparando.set(true);
    try {
      const r = await this.api.prepararXubioAExcel(l, f.operacion, f.empresa);
      if (r.zip) {
        const bytes = Uint8Array.from(atob(r.zip.contenido), (c) => c.charCodeAt(0));
        descargarBlob(r.zip.nombre, new Blob([bytes], { type: 'application/zip' }));
      }
      this.xubio.update((m) => ({ ...m, [l.archivo]: { listo: r.listo, avisos: r.avisos, nombre: r.zip?.nombre ?? '' } }));
    } catch (err) {
      this.toast.error('No se pudo preparar para Xubio', mensajeDeError(err, ''));
    } finally {
      this.preparando.set(false);
    }
  }

  protected elegirOperacion(operacion: Operacion): void {
    const l = this.actual();
    const f = this.estudio();
    if (l && f) this.elegido.update((m) => ({ ...m, [l.archivo]: { empresa: f.empresa, operacion } }));
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

  protected resumenBien(): string {
    return this.bien().map((v) => v.mensaje.replace(/\.$/, '')).join(' · ');
  }

  protected importeCorregido(x: ItemLeido): boolean {
    return x.importe_leido !== null && x.importe_leido !== undefined && x.importe_leido !== x.importe;
  }

  protected nivel(campo: string): string {
    return nivelDe(this.actual()?.verificaciones ?? [], campo) ?? '';
  }

  /** El estado de un campo: el de sus verificaciones o, si está vacío y no puede, «para mirar». */
  protected estadoCampo(campo: string, valor: string | number | null | undefined): NivelVerificacion | null {
    const nivel = nivelDe(this.actual()?.verificaciones ?? [], campo);
    if (nivel) return nivel;
    return OBLIGATORIOS.has(campo) && (valor === null || valor === undefined || valor === '') ? 'aviso' : null;
  }

  /** Por qué el campo está como está (para el title y los lectores de pantalla). */
  protected explicar(campo: string, estado: NivelVerificacion | null): string | null {
    if (!estado) return null;
    const mensajes = (this.actual()?.verificaciones ?? []).filter((v) => v.campo === campo && v.nivel === estado).map((v) => v.mensaje);
    if (mensajes.length) return mensajes.join(' ');
    return estado === 'aviso' ? 'Vacío: completalo mirando el comprobante.' : null;
  }

  /** Las alícuotas para elegir (más la actual si no es una de las habituales). */
  protected alicuotasDe(actual: number | null): number[] {
    return actual === null || ALICUOTAS_IVA.includes(actual) ? ALICUOTAS_IVA : [actual, ...ALICUOTAS_IVA];
  }

  protected formatear(v: string | number): string {
    return typeof v === 'number' ? aTexto(v) : v;
  }

  /** La etiqueta de color de cada archivo (como Ventas / Compras en «Subir a Xubio»). */
  protected nombreTipo(l: LecturaExcel): string {
    return l.error ? 'No se leyó' : l.tipo === 'comprobante' ? 'Factura' : 'Tabla';
  }

  /** Lo que queda por revisar del archivo: errores y avisos. */
  protected pendientes(l: LecturaExcel): number {
    return l.error ? 0 : this.errores(l) + this.avisos(l);
  }

  /** La tarjeta del inicio y «+ Agregar» abren el mismo selector de archivos. */
  protected abrirSelector(entrada: HTMLInputElement): void {
    if (this.leyendo()) return;
    entrada.value = '';
    entrada.click();
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
    this.olvidarXubio(nueva.archivo);
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
    const nuevo = campo === 'codigo' || campo === 'descripcion' || campo === 'producto' ? valor : aNumero(valor);
    // Si corrige la descripción a mano, deja de figurar como autocorregida.
    const extra = campo === 'descripcion' ? { leido: '' } : campo === 'importe' ? { importe_leido: null } : {};
    this.reemplazar({ ...l, items: l.items.map((x, j) => (j === i ? { ...x, [campo]: nuevo, ...extra } : x)) });
  }

  private cambiarDesglose(cambio: (filas: AlicuotaIva[]) => AlicuotaIva[]): void {
    const l = this.actual();
    if (l) this.reemplazar(conDesglose(l, cambio(l.iva_alicuotas ?? [])));
  }

  protected cambiarAlicuota(i: number, valor: string): void {
    const alicuota = valor === '' ? null : Number(valor);
    this.cambiarDesglose((filas) => filas.map((f, j) => (j === i ? { ...f, alicuota } : f)));
  }

  /** Con la base nueva y el IVA vacío, el IVA sale de la cuenta (base × alícuota). */
  protected cambiarFilaIva(i: number, campo: 'base' | 'importe', valor: string): void {
    const n = aNumero(valor);
    this.cambiarDesglose((filas) =>
      filas.map((f, j) => {
        if (j !== i) return f;
        const nueva = { ...f, [campo]: n };
        if (campo === 'base' && n !== null && f.importe === null && f.alicuota !== null) {
          nueva.importe = Math.round(n * f.alicuota) / 100;
        }
        return nueva;
      }),
    );
  }

  private cambiarTributos(cambio: (filas: TributoLeido[]) => TributoLeido[]): void {
    const l = this.actual();
    if (l) this.reemplazar(conTributos(l, cambio(l.tributos ?? [])));
  }

  protected cambiarTributo(i: number, campo: keyof TributoLeido, valor: string): void {
    const nuevo = campo === 'codigo' || campo === 'descripcion' ? valor.trim() : aNumero(valor);
    this.cambiarTributos((filas) => filas.map((f, j) => (j === i ? { ...f, [campo]: nuevo } : f)));
  }

  /** La primera fila nueva arranca con lo que ya decía «Otros tributos». */
  protected agregarTributo(): void {
    const otros = this.actual()?.totales['otros_tributos'] ?? null;
    this.cambiarTributos((filas) => [...filas, { codigo: '', descripcion: '', base: null, importe: filas.length ? null : otros || null }]);
  }

  /** La columna del Excel donde suma ese tributo. */
  protected columnaTributo(t: TributoLeido): string {
    return COLUMNA_TRIBUTO[grupoTributo(t)];
  }

  /** 6717,98 sobre 83974,76 → «8%». Null si no hay con qué calcularlo. */
  protected porcentaje(t: TributoLeido): string | null {
    if (t.base === null || t.importe === null || !t.base) return null;
    const p = Math.round((t.importe / t.base) * 10000) / 100;
    return `${String(p).replace('.', ',')}%`;
  }

  protected sacarTributo(i: number): void {
    this.cambiarTributos((filas) => filas.filter((_, j) => j !== i));
  }

  protected agregarAlicuota(): void {
    this.cambiarDesglose((filas) => [...filas, { alicuota: alicuotaLibre(filas), base: null, importe: null }]);
  }

  protected sacarAlicuota(i: number): void {
    this.cambiarDesglose((filas) => filas.filter((_, j) => j !== i));
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

  protected async generar(): Promise<void> {
    const l = this.actual();
    if (!l) return;
    this.generando.set(true);
    try {
      const est = this.estudio();
      // Los productos del detalle van al Excel para Xubio.
      const extras = { condicion: 'Cuenta Corriente', vencimiento: '', productos: l.items.map((x) => x.producto ?? ''), centros: [], dto: [] };
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
