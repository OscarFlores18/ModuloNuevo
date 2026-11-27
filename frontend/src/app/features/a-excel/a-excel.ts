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
      --cf-texto: #0f172a; --cf-suave: #475569; --cf-tenue: #64748b; --cf-borde: #e2e8f0; --cf-borde-in: #cbd5e1;
      --cf-fondo: #f8fafc; --cf-acento: #1d4ed8; --cf-acento-suave: #eff6ff;
      --cf-error: #b91c1c; --cf-error-fondo: #fef2f2; --cf-aviso: #92400e; --cf-aviso-fondo: #fffbeb; --cf-ok: #15803d; --cf-ok-fondo: #f0fdf4;
      display: flex; flex-direction: column; gap: 16px; flex: 1 0 auto; color: var(--cf-texto); font-size: .92rem;
      /* Los textos solo para lectores de pantalla (.sr-only, absolutos) quedan adentro del área que
         se desplaza: sin esto se ubicaban contra la ventana y estiraban la página con un espacio vacío. */
      position: relative;
    }
    button { font: inherit; }
    :is(button, select, input, [role='button']):focus-visible { outline: 2px solid var(--cf-acento); outline-offset: 2px; }

    /* Botones */
    .cf-btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 38px; padding: 0 16px; border: 1px solid transparent; border-radius: 8px; font-weight: 600; cursor: pointer; white-space: nowrap; }
    .cf-btn:disabled { opacity: .5; cursor: not-allowed; }
    .cf-btn-prim { background: var(--cf-acento); color: #fff; }
    .cf-btn-prim:hover:not(:disabled) { background: #1e40af; }
    .cf-btn-sec { background: #fff; color: var(--cf-texto); border-color: var(--cf-borde-in); }
    .cf-btn-sec:hover:not(:disabled) { background: var(--cf-fondo); }
    .cf-btn-chico { min-height: 30px; padding: 0 10px; font-size: .84rem; }
    .cf-link { border: 0; background: none; padding: 0; color: var(--cf-acento); font-weight: 600; cursor: pointer; }
    .cf-link:hover { text-decoration: underline; }
    .cf-x { display: inline-grid; place-items: center; width: 30px; height: 30px; border: 0; border-radius: 6px; background: transparent; color: #94a3b8; cursor: pointer; }
    .cf-x:hover { background: var(--cf-error-fondo); color: var(--cf-error); }
    .cf-x svg { width: 14px; height: 14px; }
    .cf-spinner { width: 16px; height: 16px; border-radius: 50%; border: 2px solid currentColor; border-right-color: transparent; animation: cf-gira .8s linear infinite; }
    @keyframes cf-gira { to { transform: rotate(360deg); } }

    /* Sin archivos */
    .cf-inicio { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; width: 100%; max-width: 680px; margin: 24px auto auto; padding: 48px 24px; background: #fff; border: 2px dashed var(--cf-borde-in); border-radius: 14px; text-align: center; cursor: pointer; }
    .cf-inicio:hover, .cf-inicio.encima { border-color: var(--cf-acento); background: var(--cf-acento-suave); }
    .cf-inicio-i { width: 40px; height: 40px; margin-bottom: 4px; color: var(--cf-acento); }
    .cf-inicio-t { font-size: 1.15rem; font-weight: 700; }
    .cf-inicio-s { color: var(--cf-suave); }
    .cf-inicio-f { font-size: .82rem; color: var(--cf-tenue); }
    kbd { padding: 1px 6px; border: 1px solid var(--cf-borde-in); border-bottom-width: 2px; border-radius: 4px; background: #fff; font: inherit; font-size: .78rem; }
    .cf-inicio-err { width: 100%; max-width: 680px; margin: 0 auto auto; }

    /* 1. Archivos */
    .cf-archivos { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 8px; background: #fff; border: 1px solid var(--cf-borde); border-radius: 12px; }
    .cf-archivos.encima { border-color: var(--cf-acento); background: var(--cf-acento-suave); }
    .cf-archivos ul { display: contents; list-style: none; }
    .cf-chip { display: flex; align-items: center; max-width: 280px; border: 1px solid var(--cf-borde); border-radius: 9px; background: var(--cf-fondo); }
    .cf-chip.activo { border-color: var(--cf-acento); background: var(--cf-acento-suave); box-shadow: inset 0 -2px 0 var(--cf-acento); }
    .cf-chip-btn { display: flex; align-items: center; gap: 8px; min-width: 0; padding: 6px 4px 6px 10px; border: 0; background: none; text-align: left; cursor: pointer; }
    .cf-chip-txt { display: flex; flex-direction: column; min-width: 0; line-height: 1.2; }
    .cf-chip-nombre { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; font-size: .86rem; }
    .cf-chip-estado { font-size: .76rem; color: var(--cf-tenue); }
    .cf-chip-estado.error { color: var(--cf-error); } .cf-chip-estado.aviso { color: var(--cf-aviso); } .cf-chip-estado.ok { color: var(--cf-ok); }
    .cf-punto { flex: none; width: 8px; height: 8px; border-radius: 50%; background: #22c55e; }
    .cf-punto.aviso { background: #f59e0b; } .cf-punto.error { background: #dc2626; }
    .cf-archivos-fin { display: flex; align-items: center; gap: 12px; margin-left: auto; }
    .cf-leyendo { display: inline-flex; align-items: center; gap: 6px; color: var(--cf-suave); font-size: .86rem; }

    /* 2. Resumen */
    .cf-resumen { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 14px 20px; padding: 18px 20px; background: #fff; border: 1px solid var(--cf-borde); border-radius: 14px; }
    .cf-res-meta { margin: 0 0 4px; font-size: .84rem; color: var(--cf-tenue); }
    .cf-res-titulo { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; margin: 0; font-size: 1.2rem; font-weight: 700; line-height: 1.3; }
    .cf-flecha { color: var(--cf-tenue); font-weight: 400; }
    .cf-op { padding: 2px 10px; border-radius: 999px; font-size: .78rem; font-weight: 700; letter-spacing: .02em; text-transform: uppercase; background: #e0e7ff; color: #3730a3; }
    .cf-op.venta { background: #dcfce7; color: #166534; }
    .cf-res-total { margin: 8px 0 0; color: var(--cf-suave); }
    .cf-res-total strong { margin-left: 6px; font-size: 1.5rem; color: var(--cf-texto); font-variant-numeric: tabular-nums; }
    .cf-res-acciones { display: flex; flex-wrap: wrap; align-items: flex-start; justify-content: flex-end; gap: 8px; }
    .cf-estudio { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px; padding-top: 14px; border-top: 1px solid var(--cf-borde); }
    .cf-estudio label { display: flex; align-items: center; gap: 8px; font-weight: 600; }
    .cf-estudio select { width: auto; min-width: 240px; }
    .cf-estudio-frase { flex: 1 1 300px; margin: 0; color: var(--cf-suave); }
    .cf-estudio-frase.aviso { color: var(--cf-aviso); }
    .cf-estado { grid-column: 1 / -1; padding: 12px 14px; border-radius: 10px; background: var(--cf-aviso-fondo); }
    .cf-estado.ok { background: var(--cf-ok-fondo); }
    .cf-estado.con-error { background: var(--cf-error-fondo); }
    .cf-estado-t { margin: 0; font-weight: 700; }
    .cf-estado.ok .cf-estado-t { color: var(--cf-ok); }
    .cf-estado ul { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .cf-estado li { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; }
    .cf-estado li p { margin: 0; flex: 1 1 240px; }
    .cf-ic { flex: none; display: inline-grid; place-items: center; width: 20px; height: 20px; border-radius: 50%; font-size: .72rem; font-weight: 800; color: #fff; background: #f59e0b; }
    li.error .cf-ic { background: #dc2626; }
    .cf-datos { color: var(--cf-suave); font-variant-numeric: tabular-nums; }
    .cf-xubio { grid-column: 1 / -1; margin: 0; padding: 10px 14px; border-radius: 10px; background: var(--cf-ok-fondo); color: var(--cf-ok); }
    .cf-xubio.aviso { background: var(--cf-aviso-fondo); color: var(--cf-aviso); }
    .cf-xubio p { margin: 0; } .cf-xubio p + p { margin-top: 4px; }
    .cf-alerta-lectura { grid-column: 1 / -1; }
    @media (max-width: 720px) { .cf-resumen { grid-template-columns: minmax(0, 1fr); } .cf-res-acciones { justify-content: flex-start; } }

    /* 3. Revisión */
    .cf-revision { display: flex; flex-direction: column; gap: 16px; }
    .cf-grilla { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 6fr); gap: 16px; align-items: start; }
    .cf-grilla.sin-vista { grid-template-columns: minmax(0, 1fr); }
    @media (max-width: 1000px) { .cf-grilla { grid-template-columns: minmax(0, 1fr); } }
    .cf-form { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
    .cf-bloque { background: #fff; border: 1px solid var(--cf-borde); border-radius: 14px; overflow: hidden; }
    .cf-bloque-cab { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: 4px 12px; padding: 12px 16px; border-bottom: 1px solid var(--cf-borde); }
    .cf-bloque-cab h3 { margin: 0; font-size: 1rem; font-weight: 700; }
    .cf-bloque-cab p { margin: 0; font-size: .82rem; color: var(--cf-tenue); }

    /* Campos */
    .cf-in { width: 100%; min-width: 0; height: 36px; padding: 0 10px; border: 1px solid var(--cf-borde-in); border-radius: 8px; background: #fff; color: var(--cf-texto); font: inherit; font-size: .92rem; }
    select.cf-in { padding-right: 4px; }
    .cf-in:hover { border-color: #94a3b8; }
    .cf-in:focus { outline: 2px solid var(--cf-acento); outline-offset: -1px; border-color: var(--cf-acento); }
    .cf-in.num { text-align: right; font-variant-numeric: tabular-nums; }
    .cf-in::placeholder { color: #94a3b8; }
    .cf-in.error { border-color: #dc2626; background: var(--cf-error-fondo); }
    .cf-in.aviso { border-color: #f59e0b; background: var(--cf-aviso-fondo); }
    .cf-campo { display: flex; flex-direction: column; gap: 4px; min-width: 0; font-size: .82rem; font-weight: 600; color: var(--cf-suave); }

    /* Comprobante */
    .cf-comp { display: grid; grid-template-columns: minmax(0, 1.5fr) minmax(0, 1.3fr) minmax(0, 1fr) minmax(0, 1fr); gap: 12px; padding: 14px 16px; }
    .cf-partes { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); border-top: 1px solid var(--cf-borde); }
    .cf-parte { min-width: 0; margin: 0; padding: 14px 16px; border: 0; display: flex; flex-direction: column; gap: 10px; }
    .cf-parte + .cf-parte { border-left: 1px solid var(--cf-borde); }
    .cf-parte legend { float: left; width: 100%; padding: 0; margin-bottom: 2px; font-weight: 700; }
    .cf-papel { margin-left: 6px; font-weight: 400; font-size: .84rem; color: var(--cf-tenue); }
    @media (max-width: 640px) {
      .cf-comp { grid-template-columns: minmax(0, 1fr); }
      .cf-partes { grid-template-columns: minmax(0, 1fr); }
      .cf-parte + .cf-parte { border-left: 0; border-top: 1px solid var(--cf-borde); }
    }

    /* Importes: un solo libro, como el pie de la factura */
    .cf-libro-caja { overflow-x: auto; }
    .cf-libro { width: 100%; min-width: 480px; border-collapse: collapse; }
    .cf-libro th, .cf-libro td { padding: 6px 8px; vertical-align: middle; }
    .cf-libro th:first-child, .cf-libro td:first-child { padding-left: 16px; }
    .cf-libro th:last-child, .cf-libro td:last-child { padding-right: 10px; width: 40px; }
    .cf-libro thead th { padding-top: 10px; padding-bottom: 4px; font-size: .78rem; font-weight: 600; color: var(--cf-tenue); text-align: left; }
    .cf-libro .num { text-align: right; width: 128px; }
    .cf-libro tbody th { font-weight: 500; text-align: left; }
    .cf-libro .cf-grupo-t th { padding-top: 14px; padding-bottom: 4px; font-size: .8rem; font-weight: 700; color: var(--cf-suave); text-transform: uppercase; letter-spacing: .03em; border-top: 1px solid var(--cf-borde); }
    .cf-libro .cf-grupo-t span { margin-left: 6px; font-weight: 400; text-transform: none; letter-spacing: 0; color: var(--cf-tenue); }
    .cf-nota td { padding-top: 0; }
    .cf-nota p { margin: 0; font-size: .8rem; color: var(--cf-tenue); }
    .cf-nota p.error { color: var(--cf-error); font-weight: 600; } .cf-nota p.aviso { color: var(--cf-aviso); }
    .cf-vacio td { color: var(--cf-tenue); font-size: .86rem; }
    .cf-agregar td { padding-top: 2px; padding-bottom: 8px; }
    .cf-concepto { display: flex; gap: 6px; }
    .cf-concepto .cf-cod { flex: 0 0 56px; text-align: center; }
    .cf-libro tfoot th, .cf-libro tfoot td { background: var(--cf-fondo); text-align: left; }
    .cf-libro tfoot td.num { text-align: right; }
    .cf-suma th, .cf-suma td { border-top: 1px solid var(--cf-borde); padding-top: 12px; color: var(--cf-suave); font-weight: 600; }
    .cf-suma .num { padding-right: 19px; font-variant-numeric: tabular-nums; }
    .cf-total th { font-size: 1rem; font-weight: 700; }
    .cf-total .cf-in { font-weight: 700; }
    .cf-cuadre td { padding-bottom: 12px; }
    .cf-cuadre p { margin: 0; font-weight: 600; color: var(--cf-tenue); }
    .cf-cuadre p.ok { color: var(--cf-ok); } .cf-cuadre p.error { color: var(--cf-error); }

    /* Detalle y hojas */
    .cf-tabla-caja { overflow-x: auto; }
    .cf-tabla { width: 100%; border-collapse: collapse; font-size: .9rem; }
    .cf-tabla th { padding: 8px 6px; font-size: .78rem; font-weight: 600; color: var(--cf-tenue); text-align: left; border-bottom: 1px solid var(--cf-borde); white-space: nowrap; }
    .cf-tabla th:first-child, .cf-tabla td:first-child { padding-left: 16px; }
    .cf-tabla th.num, .cf-tabla td.num { text-align: right; font-variant-numeric: tabular-nums; }
    .cf-tabla td { padding: 4px 6px; border-bottom: 1px solid #f1f5f9; }
    .cf-tabla tfoot td { padding: 10px 6px; border-bottom: 0; background: var(--cf-fondo); font-weight: 600; }
    .cf-tabla-hoja td { white-space: nowrap; }
    .cf-mas-filas { margin: 0; padding: 8px 16px; font-size: .82rem; color: var(--cf-tenue); }

    .cf-lectura { font-size: .86rem; color: var(--cf-suave); }
    .cf-lectura summary { cursor: pointer; padding: 4px 0; }
    .cf-lectura p { margin: 6px 0 0; }
    .cf-texto { margin-top: 8px; white-space: pre-wrap; font: .78rem ui-monospace, monospace; background: var(--cf-fondo); padding: 12px; border-radius: 8px; max-height: 320px; overflow: auto; color: var(--cf-texto); }
  `,
  template: `
    <app-encabezado
      titulo="Conversor De Facturas"
      bajada="Subí la factura (foto, PDF o planilla): la leo, revisás lo que haga falta y la bajás a Excel o la preparás para Xubio."
    />

    <input #entrada type="file" multiple class="sr-only" tabindex="-1" aria-hidden="true" [attr.accept]="acepta" (change)="elegir($event)" />

    @if (!lecturas().length) {
      <div
        class="cf-inicio"
        [class.encima]="encima()"
        role="button"
        tabindex="0"
        aria-describedby="cf-inicio-s"
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
          <span class="cf-spinner" aria-hidden="true"></span>
          <strong class="cf-inicio-t">Leyendo…</strong>
          <span class="cf-inicio-s">Las fotos tardan unos segundos.</span>
        } @else {
          <svg class="cf-inicio-i" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 15V4M7 9l5-5 5 5M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3" /></svg>
          <strong class="cf-inicio-t">Soltá acá tus facturas</strong>
          <span id="cf-inicio-s" class="cf-inicio-s">o hacé clic para elegirlas · también podés pegar una captura con <kbd>Ctrl</kbd> + <kbd>V</kbd></span>
          <span class="cf-inicio-f">Fotos, PDF, CSV o Excel · hasta 20 por vez</span>
        }
      </div>
      @if (error(); as err) { <app-alerta class="cf-inicio-err" nivel="error" [mensajes]="[err]" [descartable]="true" (cerrado)="error.set(null)" /> }
    } @else {
      <!-- 1. Los archivos, como pestañas -->
      <nav
        class="cf-archivos"
        [class.encima]="encima()"
        aria-label="Archivos leídos"
        data-tour="ax-archivos"
        (dragover)="$event.preventDefault(); encima.set(true)"
        (dragleave)="encima.set(false)"
        (drop)="soltar($event)"
      >
        <ul>
          @for (l of lecturas(); track l.archivo; let i = $index) {
            @let estado = estadoArchivo(l);
            <li class="cf-chip" [class.activo]="i === indice()">
              <button type="button" class="cf-chip-btn" (click)="indice.set(i)" [attr.aria-current]="i === indice() ? 'true' : null" [title]="l.archivo">
                <span class="cf-punto" [class]="estado.nivel" aria-hidden="true"></span>
                <span class="cf-chip-txt">
                  <span class="cf-chip-nombre">{{ l.archivo }}</span>
                  <span class="cf-chip-estado" [class]="estado.nivel">{{ estado.texto }}</span>
                </span>
              </button>
              <button type="button" class="cf-x" (click)="quitar(i)" [attr.aria-label]="'Quitar ' + l.archivo" [disabled]="leyendo()">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg>
              </button>
            </li>
          }
        </ul>
        <div class="cf-archivos-fin">
          @if (leyendo()) { <span class="cf-leyendo" aria-live="polite"><span class="cf-spinner" aria-hidden="true"></span> Leyendo…</span> }
          @if (lecturas().length > 1) { <button type="button" class="cf-link" (click)="limpiar()" [disabled]="leyendo()">Quitar todos</button> }
          <button type="button" class="cf-btn cf-btn-sec cf-btn-chico" (click)="abrirSelector(entrada)" [disabled]="leyendo()">+ Agregar</button>
        </div>
      </nav>
      @if (error(); as err) { <app-alerta nivel="error" [mensajes]="[err]" [descartable]="true" (cerrado)="error.set(null)" /> }

      @if (actual(); as l) {
        <!-- 2. Resumen: qué es, qué hacer y qué falta -->
        <section class="cf-resumen" aria-labelledby="cf-res-t">
          <div>
            @if (l.error) {
              <p class="cf-res-meta">No se pudo leer</p>
              <h2 id="cf-res-t" class="cf-res-titulo">{{ l.archivo }}</h2>
            } @else if (l.tipo === 'comprobante') {
              @let r = resumen();
              <p class="cf-res-meta">{{ r.cabecera }}</p>
              <h2 id="cf-res-t" class="cf-res-titulo">
                @if (estudio(); as f) { <span class="cf-op" [class.venta]="f.operacion === 'Ventas'">{{ f.operacion === 'Compras' ? 'Compra' : 'Venta' }}</span> }
                <span>{{ r.emisor }}</span><span class="cf-flecha" aria-label="a">→</span><span>{{ r.receptor }}</span>
              </h2>
              <p class="cf-res-total">Total<strong>{{ r.total }}</strong></p>
            } @else {
              <p class="cf-res-meta">Planilla · {{ l.hojas.length }} {{ l.hojas.length === 1 ? 'hoja' : 'hojas' }}</p>
              <h2 id="cf-res-t" class="cf-res-titulo">{{ l.archivo }}</h2>
            }
          </div>

          @if (!l.error) {
            <div class="cf-res-acciones">
              @if (l.tipo === 'comprobante') {
                <button type="button" class="cf-btn cf-btn-sec" (click)="prepararXubio()" [disabled]="!estudio() || preparando() || verificando()" [attr.aria-busy]="preparando()"
                  [title]="estudio() ? 'Arma el archivo para «Importar desde Mis Comprobantes (ARCA)» de Xubio' : 'Elegí primero la empresa'">
                  @if (preparando()) { <span class="cf-spinner" aria-hidden="true"></span> Preparando… } @else { Preparar para Xubio }
                </button>
              }
              <button type="button" class="cf-btn cf-btn-prim" (click)="generar()" [disabled]="generando() || verificando()" [attr.aria-busy]="generando()" data-tour="ax-generar">
                @if (generando()) { <span class="cf-spinner" aria-hidden="true"></span> Armando… } @else { Descargar Excel }
              </button>
            </div>
          }

          @if (l.error) {
            <app-alerta class="cf-alerta-lectura" nivel="error" [mensajes]="[l.error]" />
          } @else {
            @if (l.tipo === 'comprobante') {
              <div class="cf-estudio" data-tour="ax-estudio">
                <label>
                  Empresa
                  <select class="cf-in" (change)="elegirEmpresa($any($event.target).value)" [disabled]="!empresas().length">
                    <option value="" [selected]="!estudio()">{{ empresas().length ? 'Elegí la empresa' : 'Sin clientes para elegir' }}</option>
                    @for (e of empresas(); track e.nombre) { <option [value]="e.nombre" [selected]="estudio()?.empresa === e.nombre">{{ e.nombre }}</option> }
                  </select>
                </label>
                @if (estudio(); as f) {
                  <p class="cf-estudio-frase" [class.aviso]="f.origen === 'manual' || f.origen === 'trabajo'">{{ fraseOperacion() }}</p>
                  <button type="button" class="cf-link" (click)="elegirOperacion(f.operacion === 'Compras' ? 'Ventas' : 'Compras')">Es {{ f.operacion === 'Compras' ? 'venta' : 'compra' }}</button>
                } @else {
                  <p class="cf-estudio-frase aviso">{{ empresas().length ? 'Ninguna CUIT de la factura es de un cliente: elegí la empresa y te digo si es compra o venta.' : 'No se pudieron ver los clientes del estudio.' }}</p>
                }
              </div>
            }

            @if (l.avisos.length && sinDatos(l)) { <app-alerta class="cf-alerta-lectura" nivel="aviso" [mensajes]="l.avisos" /> }

            <div class="cf-estado" [class.ok]="!aMirar().length" [class.con-error]="hayErrores()" role="status" aria-live="polite">
              @if (!aMirar().length) {
                <p class="cf-estado-t">✓ Todo cuadra{{ l.tipo === 'comprobante' ? ': las cuentas dan y no falta nada.' : '.' }}</p>
              } @else {
                <p class="cf-estado-t">{{ aMirar().length }} {{ aMirar().length === 1 ? 'cosa para revisar' : 'cosas para revisar' }}</p>
                <ul>
                  @for (v of aMirar(); track $index) {
                    @let c = corta(v);
                    <li [class]="v.nivel">
                      <span class="cf-ic" aria-hidden="true">{{ v.nivel === 'error' ? '✕' : '!' }}</span>
                      <p [title]="v.mensaje">
                        <span class="sr-only">{{ v.nivel === 'error' ? 'Error: ' : 'Revisar: ' }}</span>{{ c.titulo || c.mensaje }}
                        @if (c.datos?.length) { <span class="cf-datos"> · {{ c.datos!.join(' · ') }}</span> }
                      </p>
                      @for (o of c.opciones ?? []; track o) {
                        <button type="button" class="cf-btn cf-btn-sec cf-btn-chico" (click)="elegirLectura(v.campo, o)" [title]="'Usar «' + o + '»'">{{ o }}</button>
                      }
                      @if (v.sugerencia !== null && v.sugerencia !== undefined) {
                        <button type="button" class="cf-btn cf-btn-prim cf-btn-chico" (click)="usar(v)">Usar {{ formatear(v.sugerencia) }}</button>
                      }
                    </li>
                  }
                </ul>
              }
            </div>

            @if (paraXubio(); as x) {
              <div class="cf-xubio" [class.aviso]="!x.listo || x.avisos.length" role="status">
                @if (x.listo) { <p>✓ Listo para Xubio: se descargó «{{ x.nombre }}». Falta que el bot lo cargue (todavía no está conectado).</p> }
                @for (a of x.avisos; track $index) { <p>{{ a }}</p> }
              </div>
            }
          }
        </section>

        <!-- 3. Revisión: el archivo al lado de lo leído -->
        @if (!l.error) {
          <section class="cf-revision" id="ax-revision" aria-label="Revisión" data-tour="ax-verificar">
            <div class="cf-grilla" [class.sin-vista]="!vista()">
              @if (vista(); as v) {
                <app-vista-archivo [url]="v.url" [tipo]="v.tipo" [nombre]="l.archivo" />
              }
              <div class="cf-form">
                @if (l.tipo === 'comprobante') {
                  <section class="cf-bloque" aria-labelledby="cf-comp-t">
                    <div class="cf-bloque-cab"><h3 id="cf-comp-t">Comprobante</h3></div>
                    <div class="cf-comp">
                      @for (c of camposComprobante; track c.id) {
                        <label class="cf-campo">{{ c.etiqueta }}
                          <input class="cf-in" [class]="nivel(c.id)" [attr.title]="explicar(c.id, estadoCampo(c.id, l.encabezado[c.id]))" [placeholder]="c.ayuda ?? ''" [value]="l.encabezado[c.id] ?? ''" (change)="cambiarEncabezado(c.id, $any($event.target).value)" />
                        </label>
                      }
                    </div>
                    <div class="cf-partes">
                      @for (rol of roles; track rol) {
                        <fieldset class="cf-parte">
                          <legend>{{ rol === 'emisor' ? 'Emisor' : 'Receptor' }}@if (papel(rol); as p) { <span class="cf-papel">{{ p }}</span> }</legend>
                          <label class="cf-campo">Razón social
                            <input class="cf-in" [class]="nivel(rol)" [attr.title]="explicar(rol, estadoCampo(rol, l.encabezado[rol]))" [value]="l.encabezado[rol] ?? ''" (change)="cambiarEncabezado(rol, $any($event.target).value)" />
                          </label>
                          <label class="cf-campo">CUIT
                            <input class="cf-in" inputmode="numeric" [class]="nivel(rol + '_cuit')" [attr.title]="explicar(rol + '_cuit', estadoCampo(rol + '_cuit', l.encabezado[rol + '_cuit']))" [value]="l.encabezado[rol + '_cuit'] ?? ''" (change)="cambiarEncabezado(rol + '_cuit', $any($event.target).value)" />
                          </label>
                        </fieldset>
                      }
                    </div>
                  </section>

                  <section class="cf-bloque" aria-labelledby="cf-imp-t">
                    <div class="cf-bloque-cab"><h3 id="cf-imp-t">Importes</h3><p>En el orden del pie de la factura</p></div>
                    <div class="cf-libro-caja">
                      <table class="cf-libro" aria-labelledby="cf-imp-t">
                        <thead>
                          <tr><th scope="col">Concepto</th><th scope="col" class="num">Base imponible</th><th scope="col" class="num">Importe</th><th scope="col"><span class="sr-only">Quitar</span></th></tr>
                        </thead>
                        <tbody>
                          @for (c of camposImportes(); track c.id) {
                            <tr>
                              <th scope="row"><label [for]="'cf-' + c.id">{{ c.etiqueta }}</label></th>
                              <td></td>
                              <td><input [id]="'cf-' + c.id" class="cf-in num" inputmode="decimal" [class]="nivel(c.id)" [attr.title]="explicar(c.id, estadoCampo(c.id, l.totales[c.id]))" placeholder="0,00" [value]="l.totales[c.id] ? texto(l.totales[c.id]) : ''" (change)="cambiarTotal(c.id, $any($event.target).value)" /></td>
                              <td></td>
                            </tr>
                          }
                        </tbody>
                        <tbody>
                          <tr class="cf-grupo-t"><th scope="colgroup" colspan="4">IVA</th></tr>
                          @for (f of desglose(); track $index; let i = $index) {
                            <tr>
                              <th scope="row">
                                <select class="cf-in" [attr.aria-label]="'Alícuota de la fila ' + (i + 1)" (change)="cambiarAlicuota(i, $any($event.target).value)">
                                  @if (f.alicuota === null) { <option value="" selected>Elegí…</option> }
                                  @for (a of alicuotasDe(f.alicuota); track a) { <option [value]="a" [selected]="a === f.alicuota">{{ nombreAlicuota(a) }}</option> }
                                </select>
                              </th>
                              <td><input class="cf-in num" inputmode="decimal" [attr.aria-label]="'Base imponible ' + nombreAlicuota(f.alicuota)" [value]="texto(f.base)" (change)="cambiarFilaIva(i, 'base', $any($event.target).value)" /></td>
                              <td><input class="cf-in num" inputmode="decimal" [class]="f.estado === 'ok' ? '' : f.estado" [attr.aria-label]="'Importe ' + nombreAlicuota(f.alicuota)" [attr.aria-invalid]="f.estado === 'error' ? 'true' : null" [attr.title]="f.motivo" [value]="texto(f.importe)" (change)="cambiarFilaIva(i, 'importe', $any($event.target).value)" /></td>
                              <td><button type="button" class="cf-x" (click)="sacarAlicuota(i)" [attr.aria-label]="'Quitar ' + nombreAlicuota(f.alicuota)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg></button></td>
                            </tr>
                            @if (f.estado !== 'ok') { <tr class="cf-nota"><td colspan="4"><p [class]="f.estado">{{ f.motivo }}</p></td></tr> }
                          } @empty {
                            <tr class="cf-vacio"><td colspan="4">Sin IVA discriminado (factura B, C o tique).</td></tr>
                          }
                          <tr class="cf-agregar"><td colspan="4"><button type="button" class="cf-link" (click)="agregarAlicuota()" [disabled]="!puedeAgregarAlicuota()">+ Agregar alícuota</button></td></tr>
                        </tbody>
                        <tbody>
                          <tr class="cf-grupo-t"><th scope="colgroup" colspan="4">Otros tributos<span>percepciones, impuestos internos, tasas</span></th></tr>
                          @for (t of l.tributos ?? []; track $index; let i = $index) {
                            @let estado = estadoCampo('tributo_' + i, t.importe);
                            <tr>
                              <th scope="row">
                                <div class="cf-concepto">
                                  <input class="cf-in cf-cod" [attr.aria-label]="'Código del tributo ' + (i + 1)" placeholder="Cód." [value]="t.codigo" (change)="cambiarTributo(i, 'codigo', $any($event.target).value)" />
                                  <input class="cf-in" [attr.aria-label]="'Descripción del tributo ' + (i + 1)" placeholder="Ej.: Percepción IIBB Bs. As." [attr.title]="t.descripcion || null" [value]="t.descripcion" (change)="cambiarTributo(i, 'descripcion', $any($event.target).value)" />
                                </div>
                              </th>
                              <td><input class="cf-in num" inputmode="decimal" [attr.aria-label]="'Base imponible de ' + (t.descripcion || 'el tributo ' + (i + 1))" [value]="texto(t.base)" (change)="cambiarTributo(i, 'base', $any($event.target).value)" /></td>
                              <td><input class="cf-in num" inputmode="decimal" [class]="estado === 'ok' ? '' : (estado ?? '')" [attr.aria-label]="'Importe de ' + (t.descripcion || 'el tributo ' + (i + 1))" [attr.title]="explicar('tributo_' + i, estado)" [value]="texto(t.importe)" (change)="cambiarTributo(i, 'importe', $any($event.target).value)" /></td>
                              <td><button type="button" class="cf-x" (click)="sacarTributo(i)" [attr.aria-label]="'Quitar ' + (t.descripcion || 'el tributo ' + (i + 1))"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg></button></td>
                            </tr>
                            <tr class="cf-nota"><td colspan="4"><p>En el Excel: {{ columnaTributo(t) }}@if (porcentaje(t); as p) { · {{ p }} de la base }</p></td></tr>
                          } @empty {
                            <tr class="cf-vacio"><td colspan="4">Sin otros tributos.</td></tr>
                          }
                          <tr class="cf-agregar"><td colspan="4"><button type="button" class="cf-link" (click)="agregarTributo()">+ Agregar tributo</button></td></tr>
                        </tbody>
                        <tfoot>
                          <tr class="cf-suma"><th scope="row">Suma de los importes</th><td></td><td class="num">{{ texto(cuadre().suma) }}</td><td></td></tr>
                          <tr class="cf-total">
                            <th scope="row"><label for="cf-total">Total de la factura</label></th>
                            <td></td>
                            <td><input id="cf-total" class="cf-in num" inputmode="decimal" [class]="nivel('total')" [attr.title]="explicar('total', estadoCampo('total', l.totales['total']))" [value]="texto(l.totales['total'])" (change)="cambiarTotal('total', $any($event.target).value)" /></td>
                            <td></td>
                          </tr>
                          <tr class="cf-cuadre">
                            <td colspan="4">
                              <p [class]="cuadre().estado" role="status">
                                @if (cuadre().estado === 'ok') { ✓ La suma da el total. }
                                @else if (cuadre().estado === 'error') { ✕ La suma no da el total: difiere en {{ texto(cuadre().diferencia) }}. }
                                @else { Falta el total de la factura. }
                              </p>
                            </td>
                          </tr>
                        </tfoot>
                      </table>
                    </div>
                  </section>
                } @else {
                  @for (h of l.hojas; track h.nombre) {
                    <section class="cf-bloque">
                      <div class="cf-bloque-cab"><h3>{{ h.nombre }}</h3><p>{{ h.filas.length }} filas</p></div>
                      <div class="cf-tabla-caja">
                        <table class="cf-tabla cf-tabla-hoja">
                          <tbody>
                            @for (f of h.filas.slice(0, 60); track $index) { <tr>@for (c of f; track $index) { <td>{{ c }}</td> }</tr> }
                          </tbody>
                        </table>
                      </div>
                      @if (h.filas.length > 60) { <p class="cf-mas-filas">Se muestran 60 de {{ h.filas.length }} filas; el Excel las lleva todas.</p> }
                    </section>
                  }
                }

                @if (l.texto || (l.avisos.length && !sinDatos(l))) {
                  <details class="cf-lectura">
                    <summary>Cómo se leyó{{ nombreMotor(l) ? ' · ' + nombreMotor(l) : '' }}</summary>
                    @if (!sinDatos(l)) { @for (a of l.avisos; track $index) { <p>{{ a }}</p> } }
                    @if (l.texto) { <div class="cf-texto">{{ textoSinCae(l.texto) }}</div> }
                  </details>
                }
              </div>
            </div>

            @if (l.tipo === 'comprobante') {
              <section class="cf-bloque" aria-labelledby="cf-det-t">
                <div class="cf-bloque-cab"><h3 id="cf-det-t">Detalle</h3><p>{{ l.items.length }} {{ l.items.length === 1 ? 'ítem' : 'ítems' }}</p></div>
                <div class="cf-tabla-caja">
                  <table class="cf-tabla" aria-labelledby="cf-det-t">
                    <thead><tr><th scope="col">Código</th><th scope="col">Descripción</th><th scope="col">Producto</th><th scope="col" class="num">Cantidad</th><th scope="col" class="num">Precio</th><th scope="col" class="num">IVA %</th><th scope="col" class="num">Importe</th><th scope="col"><span class="sr-only">Quitar</span></th></tr></thead>
                    <tbody>
                      @for (x of l.items; track $index; let i = $index) {
                        <tr>
                          <td style="width: 90px"><input class="cf-in" [value]="x.codigo" (change)="cambiarItem(i, 'codigo', $any($event.target).value)" [attr.aria-label]="'Código del ítem ' + (i + 1)" /></td>
                          <td><input class="cf-in" [class.aviso]="!!x.leido && x.leido !== x.descripcion" [attr.title]="x.leido && x.leido !== x.descripcion ? 'Corregido solo: decía «' + x.leido + '»' : x.descripcion || null" [value]="x.descripcion" (change)="cambiarItem(i, 'descripcion', $any($event.target).value)" [attr.aria-label]="'Descripción del ítem ' + (i + 1)" /></td>
                          <td style="width: 210px"><input class="cf-in" [attr.title]="x.producto || null" [value]="x.producto ?? ''" [attr.list]="'cf-productos'" (change)="cambiarItem(i, 'producto', $any($event.target).value)" [attr.aria-label]="'Producto del ítem ' + (i + 1)" /></td>
                          <td style="width: 90px"><input class="cf-in num" inputmode="decimal" [value]="cantidadTexto(x.cantidad)" (change)="cambiarItem(i, 'cantidad', $any($event.target).value)" [attr.aria-label]="'Cantidad del ítem ' + (i + 1)" /></td>
                          <td style="width: 120px"><input class="cf-in num" inputmode="decimal" [value]="texto(x.precio)" (change)="cambiarItem(i, 'precio', $any($event.target).value)" [attr.aria-label]="'Precio del ítem ' + (i + 1)" /></td>
                          <td style="width: 74px"><input class="cf-in num" inputmode="decimal" [value]="x.alicuota ?? ''" (change)="cambiarItem(i, 'alicuota', $any($event.target).value)" [attr.aria-label]="'IVA % del ítem ' + (i + 1)" /></td>
                          <td style="width: 130px"><input class="cf-in num" inputmode="decimal" [class.aviso]="importeCorregido(x) || nivel('item_' + i) === 'aviso'" [attr.title]="importeCorregido(x) ? 'Corregido solo: se leyó ' + texto(x.importe_leido) : explicar('item_' + i, estadoCampo('item_' + i, x.importe))" [value]="texto(x.importe)" (change)="cambiarItem(i, 'importe', $any($event.target).value)" [attr.aria-label]="'Importe del ítem ' + (i + 1)" /></td>
                          <td style="width: 40px"><button type="button" class="cf-x" (click)="sacarItem(i)" [attr.aria-label]="'Quitar el ítem ' + (i + 1)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.25" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12" /></svg></button></td>
                        </tr>
                      } @empty {
                        <tr><td colspan="8" class="cf-mas-filas">No se leyó el detalle: el Excel lleva el encabezado y los importes.</td></tr>
                      }
                    </tbody>
                    <tfoot>
                      <tr>
                        <td colspan="3"><button type="button" class="cf-link" (click)="agregarItem()">+ Agregar ítem</button></td>
                        <td colspan="3" class="num">Suma del detalle</td>
                        <td class="num">{{ texto(suma()) }}</td><td></td>
                      </tr>
                    </tfoot>
                  </table>
                  <datalist id="cf-productos">@for (p of productos; track p) { <option [value]="p"></option> }</datalist>
                </div>
              </section>
            }
          </section>
        }
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
  /** Lo que se ve en el resumen: qué comprobante es, de quién a quién y el total. */
  protected readonly resumen = computed(() => {
    const e = this.actual()?.encabezado ?? {};
    const total = this.actual()?.totales['total'];
    const cabecera = [e['tipo']?.replace(/^\d+\s*-\s*/, ''), e['numero'], e['fecha']].filter(Boolean).join(' · ');
    return {
      cabecera: cabecera || 'Comprobante',
      emisor: e['emisor'] || 'Emisor sin leer',
      receptor: e['receptor'] || (e['receptor_cuit'] ? cuitConGuiones(e['receptor_cuit']) : 'Consumidor final'),
      total: total === null || total === undefined ? '—' : '$ ' + aTexto(total),
    };
  });
  protected readonly hayErrores = computed(() => this.aMirar().some((v) => v.nivel === 'error'));
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
  /** 120 → «120», 1.5 → «1,5» (sin decimales de más). */
  protected cantidadTexto(n: number | null | undefined): string {
    return n === null || n === undefined ? '' : n.toLocaleString('es-AR', { maximumFractionDigits: 3 });
  }

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

  /** El estado de cada archivo en su pestaña. */
  protected estadoArchivo(l: LecturaExcel): { nivel: string; texto: string } {
    if (l.error) return { nivel: 'error', texto: 'No se pudo leer' };
    const n = this.pendientes(l);
    if (!n) return { nivel: 'ok', texto: l.tipo === 'comprobante' ? 'Todo cuadra' : 'Planilla' };
    return { nivel: this.errores(l) ? 'error' : 'aviso', texto: n === 1 ? '1 para revisar' : `${n} para revisar` };
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
