import { Component, OnDestroy, computed, inject, signal } from '@angular/core';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';
import { descargarBlob } from '../../core/descargar';
import { ItemLeido, LecturaExcel, Verificacion, mensajeDeError } from '../../core/models';
import { ApiService } from '../../core/services/api.service';
import { Alerta } from '../../core/ui/alerta';
import { Ayuda } from '../../core/ui/ayuda';
import { Encabezado } from '../../core/ui/encabezado';
import { ToastService } from '../../core/ui/toast';
import {
  ACEPTA, CAMPOS_ENCABEZADO, CAMPOS_TOTALES, FORMATOS_A_EXCEL, aNumero, aTexto, contar, itemVacio, nivelDe, sinDuda, sumaItems, usarSugerencia,
} from './logica';

/**
 * «Pasar a Excel»: cualquier archivo (foto, captura, PDF, CSV, Excel) a .xlsx.
 *
 * 1. Subir: POST /api/a-excel/leer → una `LecturaExcel` por archivo.
 * 2. Verificar: a la izquierda el archivo, a la derecha lo leído (editable) y lo
 *    que hay que mirar. Cada cambio vuelve a pedir POST /api/a-excel/verificar.
 * 3. Generar: POST /api/a-excel/generar → el .xlsx se descarga.
 *
 * Ver docs/pasar-a-excel.md para cómo está armado y qué falta.
 */
@Component({
  selector: 'app-a-excel',
  imports: [Encabezado, Alerta, Ayuda],
  styles: `
    :host { display: block; }
    .zona { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; padding: 28px 16px; border: 2px dashed #cbd5e1; border-radius: 12px; background: #f8fafc; text-align: center; transition: border-color .15s, background .15s; }
    .zona.encima { border-color: var(--color-acento); background: #eff6ff; }
    .ax-archivos { display: flex; flex-wrap: wrap; gap: 6px; margin: 14px 0 10px; }
    .ax-pill { display: inline-flex; align-items: center; gap: 6px; max-width: 260px; padding: 4px 10px; border: 1px solid #cbd5e1; border-radius: 999px; background: #fff; font-size: .8rem; cursor: pointer; }
    .ax-pill[aria-pressed='true'] { border-color: var(--color-acento); background: #eff6ff; font-weight: 700; }
    .ax-pill span.n { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .ax-punto { width: 8px; height: 8px; border-radius: 50%; flex: none; background: #16a34a; }
    .ax-punto.aviso { background: #f59e0b; } .ax-punto.error { background: #dc2626; }
    .ax-grilla { display: grid; grid-template-columns: minmax(0, 5fr) minmax(0, 7fr); gap: 16px; align-items: start; }
    .ax-grilla.sin-vista { grid-template-columns: minmax(0, 1fr); }
    @media (max-width: 900px) { .ax-grilla { grid-template-columns: minmax(0, 1fr); } }
    .ax-vista { position: sticky; top: 12px; border: 1px solid var(--color-borde); border-radius: 10px; background: #f1f5f9; overflow: auto; max-height: calc(100vh - 140px); }
    .ax-vista img { display: block; width: 100%; height: auto; }
    .ax-vista iframe { display: block; width: 100%; height: calc(100vh - 160px); border: 0; }
    .ax-ver { list-style: none; margin: 0 0 14px; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .ax-ver li { display: flex; gap: 8px; align-items: flex-start; padding: 8px 10px; border-radius: 8px; font-size: .84rem; line-height: 1.35; }
    .ax-ver li.error { background: #fef2f2; color: #991b1b; }
    .ax-ver li.aviso { background: #fffbeb; color: #92400e; }
    .ax-ver li.ok { background: #f0fdf4; color: #166534; }
    .ax-ver li p { margin: 0; flex: 1; }
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
    .ax-pie { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 10px; margin-top: 18px; padding-top: 12px; border-top: 1px solid var(--color-borde); }
  `,
  template: `
    <app-encabezado
      titulo="Pasar a Excel"
      bajada="Fotos, capturas, PDF, CSV o Excel viejo (.xls) a un Excel. Antes de armarlo te muestro lo que leí para que lo verifiques."
    />

    <section class="mf-panel" data-tour="ax-archivos">
      <label class="zona" [class.encima]="encima()" (dragover)="$event.preventDefault(); encima.set(true)" (dragleave)="encima.set(false)" (drop)="soltar($event)">
        <strong>Arrastrá los archivos acá o tocá para elegirlos</strong>
        <span class="mf-sub">Foto o captura (JPG, PNG), PDF (digital o escaneado), CSV, XLS o XLSX · hasta 20 por vez</span>
        <input type="file" multiple class="sr-only" [attr.accept]="acepta" (change)="elegir($event)" />
        @if (leyendo()) {
          <span class="inline-flex items-center gap-2 font-semibold"><span class="ej-spinner" aria-hidden="true"></span> Leyendo… (las fotos tardan unos segundos: se leen tres veces para comparar)</span>
        }
      </label>
      @if (error(); as err) { <app-alerta class="mt-3 block" nivel="error" [mensajes]="[err]" /> }

      @if (lecturas().length) {
        <div class="ax-archivos" role="group" aria-label="Archivos leídos">
          @for (l of lecturas(); track l.archivo; let i = $index) {
            <button type="button" class="ax-pill" [attr.aria-pressed]="i === indice()" (click)="indice.set(i)" [title]="l.archivo">
              <span class="ax-punto" [class.error]="l.error || errores(l)" [class.aviso]="!l.error && !errores(l) && avisos(l)"></span>
              <span class="n">{{ l.archivo }}</span>
            </button>
          }
        </div>
      }
    </section>

    @if (actual(); as l) {
      <section class="mf-panel mt-4" aria-labelledby="ax-titulo" data-tour="ax-verificar">
        @if (l.error) {
          <app-alerta nivel="error" [mensajes]="[l.error]" />
        } @else {
          <div class="ax-grilla" [class.sin-vista]="!vista()">
            @if (vista(); as v) {
              <div class="ax-vista" aria-label="El archivo original">
                @if (v.tipo === 'imagen') { <img [src]="v.url" [alt]="l.archivo" /> } @else { <iframe [src]="v.seguro" [title]="l.archivo"></iframe> }
              </div>
            }
            <div class="min-w-0">
              <h2 id="ax-titulo" class="m-0 text-lg font-bold">
                Verificá lo que leí
                <app-ayuda texto="Lo rojo seguro está mal (por ejemplo un CUIT que no pasa el dígito verificador o importes que no suman el total). Lo amarillo conviene compararlo con el archivo. Si las cuentas dan el valor correcto, aparece «Usar»." />
              </h2>
              <p class="mf-sub mt-1">
                {{ l.tipo === 'comprobante' ? 'Comprobante' : 'Tabla' }} · leído {{ l.metodo === 'ocr' ? 'con OCR (de la imagen)' : l.metodo === 'pdf' ? 'del texto del PDF' : 'de la planilla' }}
                @if (verificando()) { · <span class="ej-spinner" aria-hidden="true"></span> verificando… }
              </p>
              @if (l.avisos.length) { <app-alerta class="mb-2 block" nivel="aviso" [mensajes]="l.avisos" /> }

              <ul class="ax-ver" aria-live="polite">
                @for (v of aMirar(); track $index) {
                  <li [class]="v.nivel">
                    <p>{{ v.mensaje }}</p>
                    @if (v.sugerencia !== null && v.sugerencia !== undefined) {
                      <button type="button" class="mf-btn mf-btn-chico" (click)="usar(v)">Usar {{ formatear(v.sugerencia) }}</button>
                    }
                  </li>
                }
                @if (bien().length) {
                  <li class="ok"><p>{{ bien().length }} {{ bien().length === 1 ? 'cosa verificada' : 'cosas verificadas' }} bien: {{ resumenBien() }}</p></li>
                }
              </ul>

              @if (l.tipo === 'comprobante') {
                <h3 class="ax-sub">Encabezado</h3>
                <div class="ax-campos">
                  @for (c of camposEncabezado; track c.id) {
                    <label>{{ c.etiqueta }}
                      <input class="ax-in" [class]="nivel(c.id)" [value]="l.encabezado[c.id] ?? ''" (change)="cambiarEncabezado(c.id, $any($event.target).value)" />
                    </label>
                  }
                </div>

                <h3 class="ax-sub">Importes</h3>
                <div class="ax-campos">
                  @for (c of camposTotales; track c.id) {
                    <label>{{ c.etiqueta }}
                      <input class="ax-in num" [class]="nivel(c.id)" [value]="texto(l.totales[c.id])" (change)="cambiarTotal(c.id, $any($event.target).value)" />
                    </label>
                  }
                </div>

                <h3 class="ax-sub">Detalle ({{ l.items.length }} {{ l.items.length === 1 ? 'ítem' : 'ítems' }})</h3>
                <div class="ax-caja">
                  <table class="ax-tabla">
                    <thead><tr><th>Código</th><th>Descripción</th><th>Cant.</th><th>Precio</th><th>IVA %</th><th>Importe</th><th></th></tr></thead>
                    <tbody>
                      @for (x of l.items; track $index; let i = $index) {
                        <tr>
                          <td style="width: 90px"><input class="ax-in" [value]="x.codigo" (change)="cambiarItem(i, 'codigo', $any($event.target).value)" /></td>
                          <td><input class="ax-in" [value]="x.descripcion" (change)="cambiarItem(i, 'descripcion', $any($event.target).value)" /></td>
                          <td style="width: 70px"><input class="ax-in num" [value]="texto(x.cantidad)" (change)="cambiarItem(i, 'cantidad', $any($event.target).value)" /></td>
                          <td style="width: 100px"><input class="ax-in num" [value]="texto(x.precio)" (change)="cambiarItem(i, 'precio', $any($event.target).value)" /></td>
                          <td style="width: 60px"><input class="ax-in num" [value]="x.alicuota ?? ''" (change)="cambiarItem(i, 'alicuota', $any($event.target).value)" /></td>
                          <td style="width: 110px"><input class="ax-in num" [value]="texto(x.importe)" (change)="cambiarItem(i, 'importe', $any($event.target).value)" /></td>
                          <td><button type="button" class="mf-btn-link text-xs" (click)="sacarItem(i)" aria-label="Sacar el ítem">✕</button></td>
                        </tr>
                      }
                    </tbody>
                    <tfoot>
                      <tr>
                        <td colspan="2"><button type="button" class="mf-btn-link text-sm" (click)="agregarItem()">+ Agregar ítem</button></td>
                        <td colspan="3" class="text-right">Suma del detalle</td>
                        <td class="text-right">{{ texto(suma()) }}</td><td></td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
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
                  <div class="ax-texto mt-2">{{ l.texto }}</div>
                </details>
              }

              <div class="ax-pie">
                @if (errores(l)) { <span class="text-sm font-semibold text-red-700">Quedan {{ errores(l) }} {{ errores(l) === 1 ? 'error' : 'errores' }} sin corregir.</span> }
                <button type="button" class="mf-btn" (click)="generar()" [disabled]="generando() || verificando()" data-tour="ax-generar">
                  {{ generando() ? 'Armando…' : errores(l) ? 'Generar el Excel igual' : 'Generar Excel' }}
                </button>
              </div>
            </div>
          </div>
        }
      </section>
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
  protected readonly aMirar = computed(() => (this.actual()?.verificaciones ?? []).filter((v) => v.nivel !== 'ok'));
  protected readonly bien = computed(() => (this.actual()?.verificaciones ?? []).filter((v) => v.nivel === 'ok'));
  protected readonly suma = computed(() => sumaItems(this.actual()?.items ?? []));

  protected readonly errores = (l: LecturaExcel) => contar(l, 'error');
  protected readonly avisos = (l: LecturaExcel) => contar(l, 'aviso');
  protected readonly texto = aTexto;

  ngOnDestroy(): void {
    Object.values(this.vistas()).forEach((v) => URL.revokeObjectURL(v.url));
  }

  protected resumenBien(): string {
    return this.bien().map((v) => v.mensaje.replace(/\.$/, '')).join(' · ');
  }

  protected nivel(campo: string): string {
    return nivelDe(this.actual()?.verificaciones ?? [], campo) ?? '';
  }

  protected formatear(v: string | number): string {
    return typeof v === 'number' ? aTexto(v) : v;
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

  protected async generar(): Promise<void> {
    const l = this.actual();
    if (!l) return;
    this.generando.set(true);
    try {
      const r = await this.api.generarAExcel(l);
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
