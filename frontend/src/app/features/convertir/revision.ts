import { Component, computed, input, model, output, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ArchivoLeido, ComprobanteExtraido, ParteComprobante } from '../../core/models';
import { Alerta } from '../../core/ui/alerta';
import { Ayuda } from '../../core/ui/ayuda';
import { CAMPOS_IMPORTE, METODOS, TIPOS, TOTAL, bloquea, conContraparte, contraparte, recalcularTotales, validarComprobante } from './comprobantes';

/**
 * Revisión de los comprobantes leídos de PDF, fotos, CSV y planillas (Convertir).
 * Todo se puede corregir; los avisos se recalculan al editar. «Preparar para
 * Xubio» los arma igual que un AUX y siguen por el paso «Subilos a Xubio».
 */
@Component({
  selector: 'app-revision-comprobantes',
  imports: [FormsModule, Alerta, Ayuda],
  styles: `
    :host { display: block; }
    .rv-cab { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 14px; padding-bottom: 12px; border-bottom: 1px solid var(--color-borde); }
    .rv-masivo { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; margin: 12px 0; font-size: .85rem; }
    .rv-caja { border: 1px solid var(--color-borde); border-radius: 10px; overflow-x: auto; background: #fff; }
    .rv-tabla { width: 100%; min-width: 1180px; border-collapse: collapse; font-size: .8rem; }
    .rv-tabla th { text-align: left; font-size: .72rem; font-weight: 700; color: #64748b; padding: 7px 6px; background: #f8fafc; border-bottom: 1px solid #e2e8f0; white-space: nowrap; }
    .rv-tabla td { padding: 4px 6px; border-bottom: 1px solid #f1f5f9; vertical-align: middle; }
    .rv-tabla tr.rv-fuera td { opacity: .45; }
    .rv-tabla tr.rv-detalle td { background: #f8fafc; padding: 10px 12px 14px; }
    .rv-in { width: 100%; min-width: 0; padding: 3px 5px; border: 1px solid #cbd5e1; border-radius: 5px; font: inherit; background: #fff; }
    .rv-in:focus { outline: 2px solid var(--color-acento); outline-offset: -1px; }
    .rv-in.num { text-align: right; font-variant-numeric: tabular-nums; }
    .rv-in.mal { border-color: #f59e0b; background: #fffbeb; }
    .rv-archivo { max-width: 180px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .rv-metodo { display: inline-block; margin-right: 4px; padding: 0 5px; border-radius: 4px; font-size: .68rem; font-weight: 800; background: #e0f2fe; color: #075985; }
    .rv-metodo.ocr, .rv-metodo.manual { background: #fef3c7; color: #92400e; }
    .rv-estado { border: 0; background: none; padding: 2px 4px; cursor: pointer; font-weight: 700; white-space: nowrap; }
    .rv-estado.ok { color: var(--color-ok-texto); }
    .rv-estado.aviso { color: #b45309; }
    /* El detalle queda a la vista aunque la tabla sea más ancha que la pantalla. */
    .rv-fijo { position: sticky; left: 12px; max-width: min(920px, calc(100vw - 120px)); }
    .rv-grilla { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 8px 12px; }
    .rv-grilla label { display: flex; flex-direction: column; gap: 2px; font-size: .72rem; font-weight: 700; color: #64748b; }
    .rv-sub { margin: 12px 0 6px; font-size: .78rem; font-weight: 700; color: var(--color-tenue); }
    .rv-sub:first-child { margin-top: 0; }
    .rv-avisos { margin: 10px 0 0; padding-left: 18px; color: #92400e; font-size: .8rem; }
  `,
  template: `
    <section class="mf-panel" aria-labelledby="t-revision" data-tour="revision">
      <div class="rv-cab">
        <div class="min-w-0 flex-1">
          <h2 id="t-revision" class="m-0 text-lg font-bold">Revisá lo que se leyó<app-ayuda texto="Lo leído de PDF, fotos, CSV y planillas. Corregí lo que haga falta: lo marcado en amarillo conviene mirarlo. Lo destildado no se sube." /></h2>
          <p class="mf-sub m-0 mt-1">
            {{ comprobantes().length }} {{ comprobantes().length === 1 ? 'comprobante' : 'comprobantes' }} · {{ conAvisos() }} para mirar.
            Tocá el estado de una fila para ver el detalle (IVA, CAE, emisor y receptor).
          </p>
        </div>
        <button type="button" class="mf-btn inline-flex items-center gap-2" (click)="preparar.emit()" [disabled]="preparando() || !listos().length || !!motivo()" [title]="motivo() ?? ''" [attr.aria-busy]="preparando()">
          @if (preparando()) {
            <span class="inline-block h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-white/40 border-t-white" aria-hidden="true"></span>
          }
          {{ preparando() ? 'Preparando…' : 'Preparar para Xubio (' + listos().length + ')' }}
        </button>
      </div>

      @for (a of informe(); track a.nombre) {
        @if (a.avisos.length) {
          <app-alerta class="mt-3 block" [nivel]="a.tipo === 'error' ? 'error' : 'info'" [titulo]="a.nombre" [mensajes]="a.avisos" />
        }
      }
      @if (motivo(); as m) { <p class="m-0 mt-3 text-sm" style="color: #b45309">ⓘ {{ m }}</p> }

      <div class="rv-masivo">
        <span class="mf-tenue">Para todos los tildados:</span>
        <select class="rv-in" style="width: auto" aria-label="Operación para todos" [ngModel]="''" (ngModelChange)="aTodos({ operacion: $event })">
          <option value="" disabled>Venta o compra…</option>
          <option value="Ventas">Venta</option>
          <option value="Compras">Compra</option>
        </select>
        <select class="rv-in" style="width: auto; max-width: 260px" aria-label="Cliente para todos" [ngModel]="''" (ngModelChange)="aTodos({ cliente: $event })">
          <option value="" disabled>Cliente…</option>
          @for (n of clientes(); track n) { <option [value]="n">{{ n }}</option> }
        </select>
      </div>

      <div class="rv-caja">
        <table class="rv-tabla">
          <thead>
            <tr>
              <th><input type="checkbox" aria-label="Incluir todos" [checked]="todosIncluidos()" (change)="incluirTodos($any($event.target).checked)" /></th>
              <th>Archivo</th><th>Operación</th><th>Cliente</th><th>Fecha</th><th>Tipo</th><th>P. venta</th><th>Número</th>
              <th>CUIT contraparte</th><th>Razón social</th><th class="text-right">Total</th><th>Estado</th>
            </tr>
          </thead>
          <tbody>
            @for (c of comprobantes(); track c.id; let i = $index) {
              @let otra = contraparte(c);
              <tr [class.rv-fuera]="!c.incluir">
                <td><input type="checkbox" [attr.aria-label]="'Incluir ' + c.archivo" [checked]="c.incluir" (change)="cambiar(i, { incluir: $any($event.target).checked })" /></td>
                <td class="rv-archivo" [title]="c.archivo + (c.pagina ? ' · página ' + c.pagina : '')">
                  <span class="rv-metodo" [class]="'rv-metodo ' + c.metodo" [title]="metodos[c.metodo].ayuda">{{ metodos[c.metodo].texto }}</span>{{ c.archivo }}
                </td>
                <td style="width: 92px">
                  <select class="rv-in" [class.mal]="!c.operacion" aria-label="Operación" [ngModel]="c.operacion ?? ''" (ngModelChange)="cambiar(i, { operacion: $event || null })">
                    <option value="">—</option><option value="Ventas">Venta</option><option value="Compras">Compra</option>
                  </select>
                </td>
                <td style="width: 150px">
                  <select class="rv-in" [class.mal]="!c.cliente" aria-label="Cliente" [ngModel]="c.cliente" (ngModelChange)="cambiar(i, { cliente: $event })">
                    <option value="">—</option>
                    @if (c.cliente && !clientes().includes(c.cliente)) { <option [value]="c.cliente">{{ c.cliente }}</option> }
                    @for (n of clientes(); track n) { <option [value]="n">{{ n }}</option> }
                  </select>
                </td>
                <td style="width: 128px"><input type="date" class="rv-in" [class.mal]="!c.fecha" aria-label="Fecha" [ngModel]="c.fecha" (ngModelChange)="cambiar(i, { fecha: $event ?? '' })" /></td>
                <td style="width: 140px">
                  <select class="rv-in" [class.mal]="c.tipo == null" aria-label="Tipo" [ngModel]="c.tipo" (ngModelChange)="cambiar(i, { tipo: $event })">
                    <option [ngValue]="null">—</option>
                    @for (t of tipos; track t.codigo) { <option [ngValue]="t.codigo">{{ t.nombre }}</option> }
                  </select>
                </td>
                <td style="width: 70px"><input type="number" min="0" class="rv-in num" [class.mal]="!c.punto_venta" aria-label="Punto de venta" [ngModel]="c.punto_venta" (ngModelChange)="cambiar(i, { punto_venta: entero($event) })" /></td>
                <td style="width: 96px"><input type="number" min="0" class="rv-in num" [class.mal]="!c.numero" aria-label="Número" [ngModel]="c.numero" (ngModelChange)="cambiar(i, { numero: entero($event) })" /></td>
                <td style="width: 116px"><input class="rv-in" inputmode="numeric" aria-label="CUIT contraparte" [ngModel]="otra.doc_nro" (ngModelChange)="cambiarContraparte(i, { doc_nro: soloDigitos($event) })" /></td>
                <td><input class="rv-in" aria-label="Razón social" [ngModel]="otra.nombre" (ngModelChange)="cambiarContraparte(i, { nombre: $event })" /></td>
                <td style="width: 110px"><input type="number" step="0.01" class="rv-in num" [class.mal]="c.importes[total] == null" aria-label="Total" [ngModel]="c.importes[total]" (ngModelChange)="cambiarImporte(i, total, $event)" /></td>
                <td>
                  <button type="button" class="rv-estado" [class.ok]="!c.avisos.length" [class.aviso]="c.avisos.length" [attr.aria-expanded]="abiertos().has(c.id)" (click)="alternar(c.id)" [title]="c.avisos.join('\\n')">
                    {{ c.avisos.length ? '⚠ ' + c.avisos.length : '✓' }} {{ abiertos().has(c.id) ? '▴' : '▾' }}
                  </button>
                </td>
              </tr>
              @if (abiertos().has(c.id)) {
                <tr class="rv-detalle">
                  <td colspan="12">
                    <div class="rv-fijo">
                    <p class="rv-sub">Emisor y receptor</p>
                    <div class="rv-grilla">
                      <label>CUIT emisor<input class="rv-in" [ngModel]="c.emisor.doc_nro" (ngModelChange)="cambiarParte(i, 'emisor', { doc_nro: soloDigitos($event) })" /></label>
                      <label>Razón social emisor<input class="rv-in" [ngModel]="c.emisor.nombre" (ngModelChange)="cambiarParte(i, 'emisor', { nombre: $event })" /></label>
                      <label>Documento receptor
                        <select class="rv-in" [ngModel]="c.receptor.doc_tipo" (ngModelChange)="cambiarParte(i, 'receptor', { doc_tipo: $event })">
                          <option [ngValue]="null">—</option><option [ngValue]="80">CUIT</option><option [ngValue]="86">CUIL</option><option [ngValue]="96">DNI</option><option [ngValue]="99">Consumidor final</option>
                        </select>
                      </label>
                      <label>Nro. receptor<input class="rv-in" [ngModel]="c.receptor.doc_nro" (ngModelChange)="cambiarParte(i, 'receptor', { doc_nro: soloDigitos($event) })" /></label>
                      <label>Razón social receptor<input class="rv-in" [ngModel]="c.receptor.nombre" (ngModelChange)="cambiarParte(i, 'receptor', { nombre: $event })" /></label>
                      <label>CAE<input class="rv-in" inputmode="numeric" [ngModel]="c.cae" (ngModelChange)="cambiar(i, { cae: soloDigitos($event) })" /></label>
                      <label>Moneda
                        <select class="rv-in" [ngModel]="c.moneda" (ngModelChange)="cambiar(i, { moneda: $event })"><option value="$">Pesos</option><option value="DOL">Dólares</option></select>
                      </label>
                      <label>Tipo de cambio<input type="number" step="0.000001" min="0" class="rv-in num" [ngModel]="c.tipo_cambio" (ngModelChange)="cambiar(i, { tipo_cambio: $event || 1 })" /></label>
                    </div>
                    <p class="rv-sub">Importes</p>
                    <div class="rv-grilla">
                      @for (f of camposImporte; track f.clave) {
                        <label>{{ f.etiqueta }}<input type="number" step="0.01" class="rv-in num" [ngModel]="c.importes[f.clave]" (ngModelChange)="cambiarImporte(i, f.clave, $event)" /></label>
                      }
                    </div>
                    @if (c.avisos.length) {
                      <ul class="rv-avisos">@for (a of c.avisos; track a) { <li>{{ a }}</li> }</ul>
                    }
                    </div>
                  </td>
                </tr>
              }
            }
          </tbody>
        </table>
      </div>
    </section>
  `,
})
export class RevisionComprobantes {
  readonly comprobantes = model.required<ComprobanteExtraido[]>();
  readonly informe = input<ArchivoLeido[]>([]);
  readonly clientes = input<string[]>([]);
  readonly preparando = input(false);
  readonly preparar = output<void>();

  protected readonly tipos = TIPOS;
  protected readonly metodos = METODOS;
  protected readonly camposImporte = CAMPOS_IMPORTE;
  protected readonly total = TOTAL;
  protected readonly contraparte = contraparte;
  protected readonly abiertos = signal<Set<string>>(new Set());

  protected readonly listos = computed(() => this.comprobantes().filter((c) => c.incluir));
  protected readonly conAvisos = computed(() => this.listos().filter((c) => c.avisos.length).length);
  protected readonly todosIncluidos = computed(() => this.comprobantes().every((c) => c.incluir));
  /** Por qué todavía no se puede preparar (sin cliente o sin venta/compra). */
  protected readonly motivo = computed(() => {
    const n = this.listos().filter(bloquea).length;
    if (!n) return null;
    return n === 1 ? 'A un comprobante le falta el cliente o si es venta o compra.' : `A ${n} comprobantes les falta el cliente o si son venta o compra.`;
  });

  protected cambiar(i: number, cambios: Partial<ComprobanteExtraido>): void {
    this.reemplazar(i, (c) => ({ ...c, ...cambios }));
  }

  protected cambiarParte(i: number, rol: 'emisor' | 'receptor', cambios: Partial<ParteComprobante>): void {
    this.reemplazar(i, (c) => ({ ...c, [rol]: { ...c[rol], ...cambios } }));
  }

  protected cambiarContraparte(i: number, cambios: Partial<ParteComprobante>): void {
    this.reemplazar(i, (c) => conContraparte(c, cambios));
  }

  protected cambiarImporte(i: number, clave: string, valor: number | string | null): void {
    const n = valor === '' || valor == null || Number.isNaN(Number(valor)) ? null : Math.round(Number(valor) * 100) / 100;
    this.reemplazar(i, (c) => {
      const importes = { ...c.importes, [clave]: n };
      // Los totales de neto e IVA siguen a las alícuotas (salvo que se editen ellos).
      return clave === 'Imp. Neto Gravado Total' || clave === 'Total IVA' || clave === TOTAL ? { ...c, importes } : recalcularTotales({ ...c, importes });
    });
  }

  protected aTodos(cambios: Partial<ComprobanteExtraido>): void {
    if (!Object.values(cambios).some(Boolean)) return;
    this.comprobantes.update((l) => l.map((c) => (c.incluir ? revalidar({ ...c, ...cambios }) : c)));
  }

  protected incluirTodos(incluir: boolean): void {
    this.comprobantes.update((l) => l.map((c) => ({ ...c, incluir })));
  }

  protected alternar(id: string): void {
    this.abiertos.update((s) => {
      const nuevo = new Set(s);
      if (!nuevo.delete(id)) nuevo.add(id);
      return nuevo;
    });
  }

  protected entero(v: unknown): number | null {
    const n = parseInt(String(v ?? ''), 10);
    return Number.isNaN(n) ? null : n;
  }

  protected soloDigitos(v: unknown): string {
    return String(v ?? '').replace(/\D/g, '');
  }

  private reemplazar(i: number, cambio: (c: ComprobanteExtraido) => ComprobanteExtraido): void {
    this.comprobantes.update((l) => l.map((c, j) => (j === i ? revalidar(cambio(c)) : c)));
  }
}

function revalidar(c: ComprobanteExtraido): ComprobanteExtraido {
  return { ...c, avisos: validarComprobante(c) };
}
