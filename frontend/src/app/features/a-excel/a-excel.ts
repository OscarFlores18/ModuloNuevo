import { Component, HostListener, OnDestroy, computed, effect, inject, signal } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { descargarBlob } from '../../core/descargar';
import { AlicuotaIva, ItemLeido, LecturaExcel, NivelVerificacion, TributoLeido, Verificacion, mensajeDeError } from '../../core/models';
import { ApiService, ProductosXubio } from '../../core/services/api.service';
import { AlertasService } from '../../core/ui/alertas.service';
import { Alerta } from '../../core/ui/alerta';
import { Ayuda } from '../../core/ui/ayuda';
import { Encabezado } from '../../core/ui/encabezado';
import { VistaArchivo } from './vista-archivo';
import { corta } from './verificaciones';
import { ToastService } from '../../core/ui/toast';
import {
  ACEPTA, ALICUOTAS_IVA, CAMPOS_ENCABEZADO, CAMPOS_TOTALES, EmpresaEstudio, FORMATOS_A_EXCEL, FacturaDelEstudio, Operacion, aNumero, aTexto, adaptar, contar,
  cuitConGuiones, detectarEmpresa, empresaDeTrabajo, recordarEmpresaDeTrabajo, itemVacio, nivelDe, sinCae, sinDuda, sumaItems, usarSugerencia,
  alicuotaLibre, conDesglose, conTotal, conTributos, grupoTributo, nombreAlicuota, productosSugeridos, revisarDesglose,
  aplicarContrapartes, recordarContraparte, productoMasParecido, productoRecordado, recordarProducto, recordarProductoDeItem,
} from './logica';
import { borrar, borrarTodo, conDemora, guardar, recuperar } from './guardado';

/** Lo elegido en pantalla para cada archivo que no sale de la lectura. */
interface Extras {
  /** El producto de Xubio de cada ítem, elegido a mano (vacío: el sugerido). */
  productos: string[];
  /** Un solo producto para todo el detalle; vacío: uno por ítem; sin definir: según la operación. */
  agrupar?: string;
}

type Vista = { url: string; seguro: SafeResourceUrl; tipo: 'imagen' | 'pdf' };
type Elegido = Record<string, { empresa: string; operacion: Operacion | null }>;

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
      /* Los colores de la app (styles.css): mismo texto, bordes y azul que las demás pantallas. */
      --cf-texto: var(--color-texto); --cf-suave: #475569; --cf-tenue: var(--color-tenue); --cf-borde: var(--color-borde); --cf-borde-in: #c3cfe4;
      --cf-fondo: var(--color-panel-alt); --cf-acento: var(--color-acento); --cf-acento-suave: #eff6ff;
      --cf-error: #b91c1c; --cf-error-fondo: #fef2f2; --cf-aviso: #92400e; --cf-aviso-fondo: #fffbeb; --cf-ok: #15803d; --cf-ok-fondo: #f0fdf4;
      display: flex; flex-direction: column; gap: 16px; flex: 1 0 auto; color: var(--cf-texto); font-size: .92rem;
      /* Los textos solo para lectores de pantalla (.sr-only, absolutos) quedan adentro del área que
         se desplaza: sin esto se ubicaban contra la ventana y estiraban la página con un espacio vacío. */
      position: relative;
    }
    button:not(.mf-btn) { font: inherit; }
    :is(button, select, input, [role='button']):focus-visible { outline: 2px solid var(--cf-acento); outline-offset: 2px; }

    /* Botones: los de la app (.mf-btn, styles.css). */
    .cf-link { border: 0; background: none; padding: 0; color: var(--cf-acento); font-weight: 600; cursor: pointer; }
    .cf-link:hover { text-decoration: underline; }
    .cf-x { display: inline-grid; place-items: center; width: 30px; height: 30px; border: 0; border-radius: 6px; background: transparent; color: #94a3b8; cursor: pointer; }
    .cf-x:hover { background: var(--cf-error-fondo); color: var(--cf-error); }
    .cf-x svg { width: 14px; height: 14px; }
    .cf-spinner { width: 16px; height: 16px; border-radius: 50%; border: 2px solid currentColor; border-right-color: transparent; animation: cf-gira .8s linear infinite; }
    @keyframes cf-gira { to { transform: rotate(360deg); } }

    /* Sin archivos */
    .cf-inicio { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; width: 100%; max-width: 680px; margin: 24px auto auto; padding: 48px 24px; background: #fff; border: 2px dashed var(--cf-borde-in); border-radius: 12px; text-align: center; cursor: pointer; }
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
    .cf-resumen { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 14px 20px; padding: 18px 20px; background: #fff; border: 1px solid var(--cf-borde); border-radius: 12px; }
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
    .cf-bloque { background: #fff; border: 1px solid var(--cf-borde); border-radius: 12px; overflow: hidden; }
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
    .cf-agrupar { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 12px; padding: 10px 16px; border-bottom: 1px solid var(--cf-borde); background: var(--cf-acento-suave); font-size: .86rem; }
    .cf-agrupar label { display: flex; align-items: center; gap: 6px; font-weight: 600; }
    .cf-agrupar .cf-in { width: 300px; max-width: 100%; height: 32px; font-weight: 600; }
    .cf-agrupar small { color: var(--cf-suave); }
    .cf-grupo-t { margin: 8px 0 2px; font-weight: 600; font-size: .86rem; } .cf-grupo-t.error { color: var(--cf-error); } .cf-grupo-t.aviso { color: var(--cf-aviso); }
    .cf-lista-xubio { margin: 0; padding: 6px 16px; color: var(--cf-suave, #64748b); font-size: .82rem; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .cf-link { border: 0; background: none; padding: 0; color: #2563eb; text-decoration: underline; cursor: pointer; font: inherit; } .cf-link:disabled { color: #94a3b8; cursor: default; }
    .cf-auto-aviso { margin: 0; padding: 8px 16px; border-left: 3px solid #f59e0b; background: var(--cf-aviso-fondo); color: var(--cf-aviso); font-size: .84rem; }
    /* El menú de productos: fijo, para que no lo corte el scroll de la tabla. */
    .cf-menu { position: fixed; z-index: 50; display: flex; flex-direction: column; max-height: 300px; overflow-y: auto; padding: 6px; border: 1px solid var(--cf-borde-in); border-radius: 10px; background: #fff; box-shadow: 0 10px 28px rgba(15, 23, 42, .18); }
    .cf-menu button { padding: 6px 10px; border: 0; border-radius: 6px; background: none; color: var(--cf-texto); text-align: left; font-size: .86rem; cursor: pointer; }
    .cf-menu button:hover { background: var(--cf-acento-suave); color: var(--cf-acento); }
    .cf-menu button.activo { background: var(--cf-acento); color: #fff; }
    .cf-menu-t { margin: 6px 10px 2px; font-size: .72rem; font-weight: 700; letter-spacing: .03em; text-transform: uppercase; color: var(--cf-tenue); }
    .cf-menu-vacio { margin: 4px 10px; font-size: .82rem; color: var(--cf-tenue); }

    .cf-lectura { font-size: .86rem; color: var(--cf-suave); }
    .cf-lectura summary { cursor: pointer; padding: 4px 0; }
    .cf-lectura p { margin: 6px 0 0; }
    .cf-texto { margin-top: 8px; white-space: pre-wrap; font: .78rem ui-monospace, monospace; background: var(--cf-fondo); padding: 12px; border-radius: 8px; max-height: 320px; overflow: auto; color: var(--cf-texto); }
  `,
  template: `
    <app-encabezado
      titulo="Conversor de facturas"
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
          <strong class="cf-inicio-t">Arrastrá tus facturas acá</strong>
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
          <button type="button" class="mf-btn mf-btn-suave mf-btn-chico" (click)="abrirSelector(entrada)" [disabled]="leyendo()">+ Agregar</button>
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
                <button type="button" class="mf-btn mf-btn-suave" (click)="subirAXubio()" [disabled]="!estudio() || subiendo() || generando() || verificando()" [attr.aria-busy]="subiendo()"
                  [title]="estudio() ? 'Arma el Excel y lo importa en ' + estudio()!.empresa + ' (' + estudio()!.operacion + ' > Importar > Desde Excel)' : 'Elegí primero la empresa'">
                  @if (subiendo()) { <span class="cf-spinner" aria-hidden="true"></span> Subiendo a Xubio… } @else { Subir a Xubio }
                </button>
              }
              <button type="button" class="mf-btn" (click)="generar()" [disabled]="generando() || verificando()" [attr.aria-busy]="generando()" data-tour="ax-generar">
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
                  <button type="button" class="cf-link" (click)="f.ambigua ? invertir() : elegirOperacion(f.operacion === 'Compras' ? 'Ventas' : 'Compras')">Es {{ f.operacion === 'Compras' ? 'venta' : 'compra' }}</button>
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
                @for (g of gruposAMirar(); track g.titulo) {
                  <p class="cf-grupo-t" [class]="g.nivel">{{ g.titulo }}</p>
                  <ul>
                    @for (v of g.items; track $index) {
                      @let c = corta(v);
                      <li [class]="v.nivel">
                        <span class="cf-ic" aria-hidden="true">{{ v.nivel === 'error' ? '✕' : '✎' }}</span>
                        <p [title]="v.mensaje">
                          <span class="sr-only">{{ v.nivel === 'error' ? 'Error: ' : 'Revisar: ' }}</span>{{ c.titulo || c.mensaje }}
                          @if (c.datos?.length) { <span class="cf-datos"> · {{ c.datos!.join(' · ') }}</span> }
                        </p>
                        @for (o of c.opciones ?? []; track o) {
                          <!-- Lo que dio cada lectura: la primera es la recomendada. -->
                          <button type="button" class="mf-btn mf-btn-chico" [class.mf-btn-suave]="!$first" (click)="elegirLectura(v.campo, o)" [title]="'Usar «' + o + '»'">Usar {{ o }}</button>
                        }
                        @if (v.sugerencia !== null && v.sugerencia !== undefined) {
                          <button type="button" class="mf-btn mf-btn-chico" (click)="usar(v)">Usar {{ formatear(v.sugerencia) }}</button>
                        }
                      </li>
                    }
                  </ul>
                }
              }
            </div>

            @if (paraXubio(); as x) {
              <div class="cf-xubio" [class.aviso]="!x.ok" role="status">
                <p>{{ x.texto }}</p>
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
                @if (l.items.length) {
                  <div class="cf-agrupar">
                    <label><input type="checkbox" [checked]="!!agrupado()" (change)="agruparEn($any($event.target).checked ? sugeridoAgrupar() : '')" /> Agrupar en un solo producto</label>
                    @if (agrupado()) {
                      <input class="cf-in" [value]="agrupado()" autocomplete="off" aria-label="Producto para todo el detalle"
                        (focus)="abrirMenu('agrupar', $event)" (input)="filtro.set($any($event.target).value)" (blur)="cerrarMenu()"
                        (change)="agruparEn($any($event.target).value || sugeridoAgrupar())" />
                      <small>Una fila por alícuota; los artículos y sus cantidades van en Observaciones.</small>
                    }
                  </div>
                }
                <p class="cf-lista-xubio">
                  @if (trayendo() === estudio()?.empresa) {
                    <span class="cf-spinner" aria-hidden="true"></span> Trayendo los productos de Xubio de {{ estudio()?.empresa }}…
                  } @else if (listaXubio().length) {
                    {{ listaXubio().length }} productos de {{ estudio()?.operacion === 'Ventas' ? 'venta' : 'compra' }} de Xubio ({{ estudio()?.empresa }}){{ actualizadoXubio() ? ' · ' + actualizadoXubio() : '' }}
                  } @else if (estudio()) {
                    Sin la lista de Xubio de {{ estudio()?.empresa }}: se usa la general.
                  }
                  @if (estudio() && trayendo() !== estudio()?.empresa) {
                    <button type="button" class="cf-link" (click)="traerProductos(estudio()!.empresa)" [disabled]="!!trayendo()">{{ listaXubio().length ? 'Actualizar' : 'Traer de Xubio' }}</button>
                  }
                </p>
                @if (!agrupado() && autoAviso(); as a) { <p class="cf-auto-aviso" role="status">✎ En naranja: {{ a }}. Revisalo antes de bajar el Excel.</p> }
                <div class="cf-tabla-caja">
                  <table class="cf-tabla" aria-labelledby="cf-det-t">
                    <thead><tr><th scope="col">Código</th><th scope="col">Descripción</th><th scope="col">Producto</th><th scope="col" class="num">Cantidad</th><th scope="col" class="num">Precio</th><th scope="col" class="num">IVA %</th><th scope="col" class="num">Importe</th><th scope="col"><span class="sr-only">Quitar</span></th></tr></thead>
                    <tbody>
                      @for (x of l.items; track $index; let i = $index) {
                        <tr>
                          <td style="width: 90px"><input class="cf-in" [value]="x.codigo" (change)="cambiarItem(i, 'codigo', $any($event.target).value)" [attr.aria-label]="'Código del ítem ' + (i + 1)" /></td>
                          <td><input class="cf-in" [class.aviso]="!!x.leido && x.leido !== x.descripcion" [attr.title]="x.leido && x.leido !== x.descripcion ? 'Corregido solo: decía «' + x.leido + '»' : x.descripcion || null" [value]="x.descripcion" (change)="cambiarItem(i, 'descripcion', $any($event.target).value)" [attr.aria-label]="'Descripción del ítem ' + (i + 1)" /></td>
                          <td style="width: 230px">
                            <input class="cf-in" [class.aviso]="!agrupado() && autoProducto(i)" [value]="agrupado() || productoDe(i)" [disabled]="!!agrupado()" placeholder="Elegí o escribí" autocomplete="off"
                              [attr.title]="autoProducto(i) ? 'Autocompletado (' + origenSugerido(i) + '): revisalo' : productoDe(i) || null"
                              (focus)="abrirMenu(i, $event)" (input)="filtro.set($any($event.target).value)" (blur)="cerrarMenu()"
                              (change)="ponerProducto(i, $any($event.target).value)" [attr.aria-label]="'Producto del ítem ' + (i + 1)" />
                          </td>
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
                </div>
                @if (menu(); as m) {
                  @let actualMenu = m.i === 'agrupar' ? agrupado() : productoDe(m.i);
                  <div class="cf-menu" role="listbox" aria-label="Productos de Xubio" [style.top.px]="m.top" [style.left.px]="m.left" [style.width.px]="m.ancho" [style.max-height.px]="m.alto">
                    @if (m.i !== 'agrupar' && sugerido(m.i); as s) {
                      <p class="cf-menu-t">Sugerido · {{ origenSugerido(m.i) }}</p>
                      <button type="button" role="option" [class.activo]="actualMenu === s" (mousedown)="$event.preventDefault()" (click)="elegirDelMenu(m.i, s)">★ {{ s }}</button>
                    }
                    <p class="cf-menu-t">Productos de Xubio</p>
                    @for (p of productosFiltrados(); track p) {
                      <button type="button" role="option" [class.activo]="actualMenu === p" (mousedown)="$event.preventDefault()" (click)="elegirDelMenu(m.i, p)">{{ p }}</button>
                    } @empty {
                      <p class="cf-menu-vacio">Ninguno coincide: queda lo que escribiste.</p>
                    }
                  </div>
                }
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
  private readonly alertas = inject(AlertasService);
  private readonly sanitizer = inject(DomSanitizer);

  protected readonly acepta = ACEPTA;
  protected readonly roles = ['emisor', 'receptor'] as const;
  /** Tipo, número y fecha (emisor y receptor van aparte, cada uno en su lado). */
  protected readonly camposComprobante = CAMPOS_ENCABEZADO.filter((c) => !/^(emisor|receptor)/.test(c.id));
  protected readonly subiendo = signal(false);
  /** archivo → lo que contestó Xubio al subirlo (se borra al cambiar algo de la factura). */
  private readonly xubio = signal<Record<string, { ok: boolean; texto: string }>>({});
  protected readonly paraXubio = computed(() => {
    const l = this.actual();
    return l ? (this.xubio()[l.archivo] ?? null) : null;
  });
  /** empresa → sus productos de Xubio (Compras y Ventas), traídos por el bot. */
  private readonly listas = signal<Record<string, ProductosXubio>>({});
  /** La empresa cuyos productos se están trayendo de Xubio. */
  protected readonly trayendo = signal<string | null>(null);
  private readonly pedidas = new Set<string>();
  /** Los nombres de la lista de Xubio de la empresa y la operación de la factura. */
  protected readonly listaXubio = computed(() => {
    const f = this.estudio();
    return f ? (this.listas()[f.empresa]?.[f.operacion] ?? []).map((p) => p.nombre) : [];
  });
  protected readonly actualizadoXubio = computed(() => {
    const a = this.listas()[this.estudio()?.empresa ?? '']?.actualizado;
    return a ? 'al ' + new Date(a).toLocaleDateString('es-AR') : '';
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
  private readonly vistas = signal<Record<string, Vista>>({});
  /** archivo → espera para volver a verificarlo (cada archivo la suya: cambiar de pestaña no la pierde). */
  private readonly temporizadores = new Map<string, ReturnType<typeof setTimeout>>();

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
  /** Lo que se autorrellenó (para revisar) y lo que no se leyó bien (para completar a mano). */
  protected readonly gruposAMirar = computed(() => {
    // Autorrellenado: lo que el lector corrigió o completó solo («Autocorregí…», redondeos).
    const esAuto = (v: Verificacion) => v.nivel !== 'error' && /^(Autocorreg|Redondeo)/i.test(v.titulo || v.mensaje);
    const auto = this.aMirar().filter(esAuto);
    const mal = this.aMirar().filter((v) => !esAuto(v));
    return [
      { titulo: 'No se leyeron bien: completalos vos mirando la foto', nivel: mal.some((v) => v.nivel === 'error') ? 'error' : 'aviso', items: mal },
      { titulo: 'Se autorrellenaron: revisá que estén bien', nivel: 'aviso', items: auto },
    ].filter((g) => g.items.length);
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
  private readonly elegido = signal<Elegido>({});
  protected readonly cuit = cuitConGuiones;
  protected readonly nivelFn = (campo: string) => this.nivel(campo);
  /** Para que las sugerencias se actualicen al recordar un producto. */
  private readonly recordados = signal(0);
  /** Los productos de Xubio para elegir en el detalle: primero los que ya usó la empresa. */
  protected readonly productos = computed(() => {
    this.recordados();
    const f = this.estudio();
    return productosSugeridos(f?.empresa ?? '', f?.operacion ?? 'Compras', this.listaXubio());
  });
  /** Lo que se va escribiendo en el producto: filtra el menú. */
  protected readonly filtro = signal('');
  protected readonly productosFiltrados = computed(() => {
    const clave = (x: string) => x.toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '');
    const f = clave(this.filtro().trim());
    const todos = this.productos();
    // Recién abierto (con el producto ya puesto) se ven todos; al escribir, los que coinciden.
    return !f || todos.some((p) => clave(p) === f) ? todos : todos.filter((p) => clave(p).includes(f));
  });

  /** archivo → lo elegido en pantalla (productos y agrupar). */
  private readonly extrasPorArchivo = signal<Record<string, Extras>>({});
  protected readonly extras = computed<Extras>(() => {
    const l = this.actual();
    return (l && this.extrasPorArchivo()[l.archivo]) || { productos: [] };
  });

  /** Lo sugerido para el ítem: lo que se eligió antes para esa descripción o lo que propuso la IA. */
  protected sugerido(i: number): string {
    const x = this.actual()?.items[i];
    if (!x) return '';
    const crudo = productoRecordado(this.estudio()?.empresa ?? '', x.descripcion) || x.producto || '';
    const lista = this.listaXubio();
    // Con la lista de Xubio, solo productos que existen ahí (si no, Xubio no lo importa).
    return lista.length ? productoMasParecido(crudo, lista) : crudo;
  }

  protected origenSugerido(i: number): string {
    const x = this.actual()?.items[i];
    return x && productoRecordado(this.estudio()?.empresa ?? '', x.descripcion) ? 'lo que elegiste la otra vez' : 'según la descripción';
  }

  /** El producto que se ve: el elegido o, si no, el sugerido (autocompletado, en naranja). */
  protected productoDe(i: number): string {
    return this.extras().productos[i] || this.sugerido(i);
  }

  protected autoProducto(i: number): boolean {
    return !this.extras().productos[i] && !!this.sugerido(i);
  }

  /** «autocompleté 8 productos». Las descripciones e importes corregidos ya se avisan arriba. */
  protected readonly autoAviso = computed(() => {
    this.extras();
    this.recordados();
    const n = (this.actual()?.items ?? []).filter((_, i) => this.autoProducto(i)).length;
    return n ? `autocompleté ${n === 1 ? 'el producto' : n + ' productos'} con lo sugerido` : '';
  });

  /** En una compra con varios artículos, por defecto todo va como un solo producto. */
  protected readonly agrupado = computed(() => {
    const l = this.actual();
    const elegido = this.extras().agrupar;
    if (elegido !== undefined) return elegido;
    return this.estudio()?.operacion === 'Compras' && (l?.items.length ?? 0) > 1 ? this.sugeridoAgrupar() : '';
  });

  /** El producto que más se repite entre los sugeridos del detalle. */
  protected sugeridoAgrupar(): string {
    const cuenta = new Map<string, number>();
    (this.actual()?.items ?? []).forEach((_, i) => {
      const p = this.sugerido(i);
      if (p) cuenta.set(p, (cuenta.get(p) ?? 0) + 1);
    });
    const [mejor] = [...cuenta].sort((a, b) => b[1] - a[1])[0] ?? [];
    const generico = 'Gastos de limpieza, libreria y refrigerio';
    const lista = this.listaXubio();
    return mejor ?? (lista.length ? productoMasParecido(generico, lista) || lista[0] : generico);
  }

  protected agruparEn(nombre: string): void {
    this.cambiarExtras((e) => ({ ...e, agrupar: nombre.trim() }));
  }

  /** `aprender`: elegido del menú. Escrito a mano solo se aprende si es un producto conocido (no «comb» a medio escribir). */
  protected ponerProducto(i: number, nombre: string, aprender = false): void {
    const producto = nombre.trim();
    const conocido = aprender || this.productos().some((p) => p.toLowerCase() === producto.toLowerCase());
    this.cambiarExtras((e) => {
      const productos = [...e.productos];
      productos[i] = producto;
      return { ...e, productos };
    });
    if (!conocido) return;
    const empresa = this.estudio()?.empresa ?? '';
    // Lo elegido para esa descripción se sugiere la próxima vez.
    recordarProductoDeItem(empresa, this.actual()?.items[i]?.descripcion ?? '', producto);
    if (empresa && producto) recordarProducto(empresa, producto, this.estudio()?.operacion ?? 'Compras');
    this.recordados.update((n) => n + 1);
  }

  private cambiarExtras(f: (e: Extras) => Extras): void {
    const l = this.actual();
    if (l) this.extrasPorArchivo.update((m) => ({ ...m, [l.archivo]: f(this.extras()) }));
  }

  /** El menú de productos abierto debajo de un campo (fijo, para que no lo corte el scroll). */
  protected readonly menu = signal<{ i: number | 'agrupar'; top: number; left: number; ancho: number; alto: number } | null>(null);
  private cierre: ReturnType<typeof setTimeout> | null = null;

  protected abrirMenu(i: number | 'agrupar', ev: FocusEvent): void {
    if (this.cierre) clearTimeout(this.cierre);
    const r = (ev.target as HTMLElement).getBoundingClientRect();
    const ancho = Math.max(r.width, 320);
    // Abajo del campo; si no entra, arriba.
    const abajo = window.innerHeight - r.bottom - 12;
    const arriba = r.top - 12;
    const alto = Math.min(300, Math.max(abajo, arriba));
    const top = abajo >= Math.min(300, 220) || abajo >= arriba ? r.bottom + 4 : r.top - 4 - alto;
    this.filtro.set('');
    this.menu.set({ i, top, left: Math.max(8, Math.min(r.left, window.innerWidth - ancho - 8)), ancho, alto });
  }

  protected cerrarMenu(): void {
    this.cierre = setTimeout(() => this.menu.set(null), 150);
  }

  protected elegirDelMenu(i: number | 'agrupar', producto: string): void {
    if (i === 'agrupar') this.agruparEn(producto);
    else this.ponerProducto(i, producto, true);
    this.menu.set(null);
    // Lo escrito para filtrar («comb») no tiene que pisar lo elegido al salir del campo.
    const campo = document.activeElement as HTMLInputElement | null;
    if (campo?.tagName === 'INPUT') campo.value = producto;
    campo?.blur();
  }

  @HostListener('window:scroll')
  protected alDesplazar(): void {
    // Fijo en la pantalla: si la página se mueve, quedaría lejos del campo.
    if (this.menu()) {
      this.menu.set(null);
      (document.activeElement as HTMLElement | null)?.blur();
    }
  }
  protected readonly textoSinCae = sinCae;
  protected readonly corta = corta;

  /** La factura vista desde la empresa del estudio (detectada o elegida). */
  protected readonly estudio = computed<FacturaDelEstudio | null>(() => {
    const l = this.actual();
    if (!l || l.tipo !== 'comprobante') return null;
    const detectada = detectarEmpresa(l.encabezado, this.empresas(), { archivo: l.archivo, trabajo: empresaDeTrabajo() });
    const manual = this.elegido()[l.archivo];
    if (!manual) {
      if (detectada) return detectada;
      const trabajo = this.empresas().find((e) => e.nombre === empresaDeTrabajo());
      return trabajo ? adaptar(l.encabezado, trabajo.nombre, 'Compras', 'trabajo') : null;
    }
    if (!manual.empresa) return null;
    // Elegida a mano: compra o venta según de qué lado de la factura está esa empresa.
    const propia = detectarEmpresa(l.encabezado, this.empresas().filter((e) => e.nombre === manual.empresa));
    const operacion = manual.operacion ?? propia?.operacion ?? 'Compras';
    const origen = propia && operacion === propia.operacion ? propia.origen : 'manual';
    // Si los dos lados son clientes, se sigue pudiendo dar vuelta.
    return { ...adaptar(l.encabezado, manual.empresa, operacion, origen), ambigua: detectada?.ambigua && origen !== 'manual' };
  });

  /** Compra o venta, dicho en una frase: quién es quién y de dónde se sacó. */
  protected readonly fraseOperacion = computed(() => {
    const f = this.estudio();
    if (!f) return '';
    const otro = f.contraparte.nombre || (f.operacion === 'Compras' ? 'el emisor' : 'consumidor final');
    switch (f.origen) {
      case 'receptor': return `${f.empresa} recibió la factura: le compró a ${otro}.${f.ambigua ? ' El emisor también es cliente del estudio.' : ''}`;
      case 'emisor': return `${f.empresa} emitió la factura: le vendió a ${otro}.${f.ambigua ? ' El receptor también es cliente del estudio.' : ''}`;
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
    void this.restaurar();
    // Cada cambio queda guardado en el navegador: recargar o irse a otra pantalla no borra nada.
    const guardarEstado = conDemora(() =>
      void guardar('estado', { lecturas: this.lecturas(), indice: this.indice(), elegido: this.elegido(), extras: this.extrasPorArchivo() }),
    );
    effect(() => {
      const empresa = this.estudio()?.empresa;
      if (empresa && !this.pedidas.has(empresa)) {
        this.pedidas.add(empresa);
        void this.cargarProductos(empresa);
      }
    });
    effect(() => {
      this.lecturas(); this.indice(); this.elegido(); this.extrasPorArchivo();
      if (this.restaurado()) guardarEstado();
    });
  }

  /** Hasta recuperar lo guardado no se guarda nada (si no, se pisaría con la página vacía). */
  private readonly restaurado = signal(false);

  private async restaurar(): Promise<void> {
    type Estado = { lecturas: LecturaExcel[]; indice: number; elegido: Elegido; extras: Record<string, Extras> };
    const e = await recuperar<Estado>('estado');
    if (e?.lecturas?.length && !this.lecturas().length) {
      const vistas: Record<string, Vista> = {};
      for (const l of e.lecturas) {
        const a = await recuperar<{ blob: Blob; tipo: 'imagen' | 'pdf' }>('archivo:' + l.archivo);
        if (a?.blob) vistas[l.archivo] = this.vistaDe(a.blob, a.tipo);
      }
      this.vistas.set(vistas);
      // Lo guardado por una versión anterior puede no traer el desglose ni los tributos.
      // Y un PDF con texto o una planilla guardados antes pueden traer avisos que ya no van.
      this.lecturas.set(e.lecturas.map((l) => ({
        ...l, iva_alicuotas: l.iva_alicuotas ?? [], tributos: l.tributos ?? [],
        verificaciones: deImagen(l) ? l.verificaciones : l.verificaciones.filter((v) => v.nivel === 'ok'),
      })));
      this.indice.set(Math.min(e.indice ?? 0, e.lecturas.length - 1));
      this.elegido.set(e.elegido ?? {});
      this.extrasPorArchivo.set(e.extras ?? {});
    }
    this.restaurado.set(true);
  }

  private vistaDe(blob: Blob, tipo: 'imagen' | 'pdf'): Vista {
    const url = URL.createObjectURL(blob);
    // El PDF va en un iframe: Angular pide marcar la URL (es un blob: de esta misma página).
    return { url, seguro: this.sanitizer.bypassSecurityTrustResourceUrl(url), tipo };
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

  /** Los productos de Xubio de la empresa: los ya traídos o, si no hay, los trae el bot. */
  private async cargarProductos(empresa: string): Promise<void> {
    try {
      const guardados = await this.api.productosXubio(empresa);
      if (guardados.Compras.length || guardados.Ventas.length) {
        this.listas.update((m) => ({ ...m, [empresa]: guardados }));
        return;
      }
    } catch {
      return;
    }
    await this.traerProductos(empresa);
  }

  protected async traerProductos(empresa: string): Promise<void> {
    if (this.trayendo()) return;
    this.trayendo.set(empresa);
    try {
      const r = await this.api.traerProductosXubio(empresa);
      this.listas.update((m) => ({ ...m, [empresa]: r }));
    } catch (err) {
      this.toast.error('No se pudieron traer los productos de Xubio', mensajeDeError(err, ''));
    } finally {
      this.trayendo.set(null);
    }
  }

  /** El Excel de Xubio (el mismo que «Descargar Excel»): los productos elegidos y el agrupado. */
  private async armarExcel(l: LecturaExcel): Promise<{ nombre: string; contenido: string }> {
    const est = this.estudio();
    const extras = {
      condicion: 'Cuenta Corriente', vencimiento: '', productos: l.items.map((_, i) => this.productoDe(i)), centros: [], dto: [],
      agrupar: this.agrupado(),
    };
    return this.api.generarAExcel(l, est ? { operacion: est.operacion, empresa: est.empresa, extras } : {});
  }

  /** Arma el .xls y el bot lo importa en Xubio (Compras o Ventas > Importar > Desde Excel). */
  protected async subirAXubio(): Promise<void> {
    const l = this.actual();
    const f = this.estudio();
    if (!l || !f || this.subiendo()) return;
    const sinProducto = this.agrupado() ? 0 : l.items.filter((_, i) => !this.productoDe(i)).length;
    const errores = this.aMirar().filter((v) => v.nivel === 'error').length;
    const avisos = [
      `El bot abre Xubio, entra a ${f.empresa} y en ${f.operacion} > Importar > Desde Excel carga el archivo y aprieta «Importar».`,
      'No se puede deshacer: si ya la subiste, no lo hagas de nuevo (quedaría repetida).',
    ];
    if (errores) avisos.unshift(`Quedan ${errores} ${errores === 1 ? 'dato mal leído' : 'datos mal leídos'} sin corregir.`);
    if (sinProducto) avisos.unshift(`${sinProducto} ${sinProducto === 1 ? 'ítem no tiene' : 'ítems no tienen'} producto de Xubio.`);
    const seguir = await this.alertas.confirmar(
      `Subir a Xubio como ${f.operacion === 'Compras' ? 'compra' : 'venta'}`,
      `${l.archivo} → ${f.empresa}`,
      'Importar en Xubio',
      avisos,
    );
    if (!seguir) return;
    this.subiendo.set(true);
    const poner = (ok: boolean, texto: string) => this.xubio.update((m) => ({ ...m, [l.archivo]: { ok, texto } }));
    try {
      const excel = await this.armarExcel(l);
      const r = await this.api.subirExcelAXubio(f.empresa, f.operacion, excel.nombre, excel.contenido);
      const dijo = r.mensaje ? ` Xubio: «${r.mensaje}».` : '';
      if (r.importado === true) {
        poner(true, `✓ Importada en Xubio (${f.empresa}, ${f.operacion}).${dijo}`);
        this.toast.ok('Importada en Xubio', `${f.operacion} · ${f.empresa}`);
      } else if (r.importado === null) {
        poner(false, `Se apretó «Importar» pero no se pudo confirmar el resultado.${dijo} Fijate en Xubio (la ventana quedó abierta) antes de subirla de nuevo.`);
        this.toast.aviso('Revisá en Xubio', 'No se pudo confirmar si se importó.');
      } else {
        poner(false, `Xubio no la importó.${dijo} Revisá la ventana de Chrome.`);
        this.toast.error('Xubio no la importó', r.mensaje || f.operacion);
      }
    } catch (err) {
      poner(false, mensajeDeError(err, 'No se pudo subir a Xubio.'));
    } finally {
      this.subiendo.set(false);
    }
  }

  /** Emisor y receptor son clientes: mirarla desde el otro lado (compra <-> venta, la otra empresa). */
  protected invertir(): void {
    const l = this.actual();
    const f = this.estudio();
    if (!l || !f) return;
    const lado = f.operacion === 'Compras' ? 'emisor' : 'receptor';
    const otra = detectarEmpresa({ [lado]: l.encabezado[lado] ?? '', [`${lado}_cuit`]: l.encabezado[`${lado}_cuit`] ?? '' }, this.empresas());
    if (otra) this.elegido.update((m) => ({ ...m, [l.archivo]: { empresa: otra.empresa, operacion: otra.operacion } }));
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
    void borrar('archivo:' + l.archivo);
    this.lecturas.update((ls) => ls.filter((_, j) => j !== i));
    const n = this.lecturas().length;
    if (this.indice() >= n) this.indice.set(Math.max(0, n - 1));
  }

  protected limpiar(): void {
    Object.values(this.vistas()).forEach((v) => URL.revokeObjectURL(v.url));
    this.vistas.set({});
    void borrarTodo();
    this.lecturas.set([]);
    this.extrasPorArchivo.set({});
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
        vistas[a.name] = this.vistaDe(a, tipo);
        void guardar('archivo:' + a.name, { blob: a, tipo });
      }
      this.vistas.set(vistas);
      // Los nuevos reemplazan a los del mismo nombre; el primero nuevo queda a la vista.
      const nombres = new Set(lecturas.map((l) => l.archivo));
      const resto = this.lecturas().filter((l) => !nombres.has(l.archivo));
      // Lo que se leyó de nuevo arranca sin lo elegido antes para ese archivo.
      this.extrasPorArchivo.update((m) => Object.fromEntries(Object.entries(m).filter(([k]) => !nombres.has(k))));
      // El proveedor o cliente corregido a mano otra vez queda como se corrigió.
      this.lecturas.set([...resto, ...lecturas.map(aplicarContrapartes)]);
      this.indice.set(resto.length);
    } catch (err) {
      this.error.set(mensajeDeError(err, 'No se pudieron leer los archivos.'));
    } finally {
      this.leyendo.set(false);
    }
  }

  // --- Corregir -----------------------------------------------------------------------

  private reemplazar(nueva: LecturaExcel): void {
    this.olvidarXubio(nueva.archivo);
    this.lecturas.update((ls) => ls.map((l) => (l.archivo === nueva.archivo ? nueva : l)));
    this.reverificar(nueva.archivo);
  }

  /** Un rato después del último cambio, la verificación otra vez en el backend (de ese archivo). */
  private reverificar(archivo: string): void {
    const previo = this.temporizadores.get(archivo);
    if (previo) clearTimeout(previo);
    this.temporizadores.set(archivo, setTimeout(async () => {
      this.temporizadores.delete(archivo);
      const l = this.lecturas().find((x) => x.archivo === archivo);
      if (!l) return;
      this.verificando.set(true);
      try {
        const { verificaciones: todas } = await this.api.verificarAExcel(l);
        // Los avisos son para lo leído de una foto o captura; un PDF con texto o una planilla vienen tal cual.
        const verificaciones = deImagen(l) ? todas : todas.filter((v) => v.nivel === 'ok');
        // Si mientras tanto cambió otra vez, se espera a la próxima verificación.
        this.lecturas.update((ls) => ls.map((x) => (x === l ? { ...x, verificaciones } : x)));
      } catch (err) {
        this.toast.error('No se pudo verificar', mensajeDeError(err, ''));
      } finally {
        this.verificando.set(false);
      }
    }, 350));
  }

  protected usar(v: Verificacion): void {
    const l = this.actual();
    // El aviso usado se va enseguida (sin esperar a que el backend vuelva a verificar).
    if (l) this.reemplazar(sinAviso(usarSugerencia(l, v), v.campo));
  }

  protected cambiarEncabezado(campo: string, valor: string): void {
    const l = this.actual();
    if (!l) return;
    // Proveedor o cliente corregido a mano: se recuerda para las próximas facturas.
    const lado = campo.replace(/_cuit$/, '');
    if (lado === 'emisor' || lado === 'receptor') {
      const e = { ...l.encabezado, [campo]: valor.trim() };
      const leido = l.corregidos?.[lado] ?? l.encabezado[lado] ?? '';
      const leidoCuit = l.corregidos?.[`${lado}_cuit`] ?? l.encabezado[`${lado}_cuit`] ?? '';
      recordarContraparte(leido, leidoCuit, e[lado] ?? '', e[`${lado}_cuit`] ?? '');
    }
    this.reemplazar(sinDuda({ ...l, encabezado: { ...l.encabezado, [campo]: valor.trim() } }, campo));
  }

  /** Entre lo que dieron las lecturas, el valor que eligió el usuario. */
  protected elegirLectura(campo: string, valor: string): void {
    const l = this.actual();
    if (l) this.lecturas.update((ls) => ls.map((x) => (x === l ? sinAviso(x, campo) : x)));
    if (campo === 'iva' || /^iva_\d+$/.test(campo) || CAMPOS_TOTALES.some((c) => c.id === campo)) this.cambiarTotal(campo, valor);
    else this.cambiarEncabezado(campo, valor);
  }

  protected cambiarTotal(campo: string, valor: string): void {
    const l = this.actual();
    if (l) this.reemplazar(conTotal(l, campo, aNumero(valor)));
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
      const r = await this.armarExcel(l);
      const bytes = Uint8Array.from(atob(r.contenido), (c) => c.charCodeAt(0));
      // El modelo de Xubio es .xls (Excel 97-2003); lo demás, .xlsx.
      const tipo = /\.xls$/i.test(r.nombre) ? 'application/vnd.ms-excel' : 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
      descargarBlob(r.nombre, new Blob([bytes], { type: tipo }));
      this.toast.ok('Excel listo', r.nombre);
    } catch (err) {
      this.toast.error('No se pudo armar el Excel', mensajeDeError(err, ''));
    } finally {
      this.generando.set(false);
    }
  }
}

/** Lo leído de una foto, captura o PDF escaneado (IA u OCR): solo ahí hay avisos. */
function deImagen(l: LecturaExcel): boolean {
  return l.metodo === 'ia' || l.metodo === 'ocr';
}

/** Sin los avisos de un campo que se acaba de corregir (la verificación nueva dirá si queda algo). */
function sinAviso(l: LecturaExcel, campo: string): LecturaExcel {
  return { ...l, verificaciones: l.verificaciones.filter((v) => v.campo !== campo || v.nivel === 'ok') };
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
