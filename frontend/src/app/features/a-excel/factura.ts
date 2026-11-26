import { Component, computed, effect, input, output, signal } from '@angular/core';
import { ItemLeido, LecturaExcel } from '../../core/models';
import { FacturaDelEstudio, aTexto, cuitConGuiones, productosSugeridos, recordarProducto } from './logica';

/** Lo que solo existe en pantalla (todavía no va al Excel ni a Xubio). */
export interface Extras {
  condicion: string;
  vencimiento: string;
  productos: string[];
  centros: string[];
  dto: number[];
}

const ALICUOTAS = [21, 10.5, 27, 5, 2.5, 0];

/**
 * La factura leída como se carga en Xubio (encabezado, ítems y totales) o como se ve
 * en ARCA (solo lectura). Desde el lado de la empresa del estudio: en una compra la
 * contraparte es el proveedor (emisor); en una venta, el cliente (receptor).
 */
@Component({
  selector: 'app-factura',
  styles: `
    :host { display: block; margin-top: 14px; }
    .fa-barra { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; margin-bottom: 10px; }
    .fa-ver { display: inline-flex; border: 1px solid #cbd5e1; border-radius: 6px; overflow: hidden; }
    .fa-ver button { border: 0; background: #fff; padding: 5px 14px; font: inherit; font-size: .85rem; font-weight: 700; color: #475569; cursor: pointer; }
    .fa-ver button + button { border-left: 1px solid #cbd5e1; }
    .fa-ver button[aria-pressed="true"] { background: #00618a; color: #fff; }
    .fa-flujo { flex: 1 1 280px; min-width: 0; display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin: 0; padding: 6px 12px; border-radius: 10px; background: linear-gradient(90deg, #e0f2fe, #f0f9ff); border: 1px solid #7dd3fc; border-left: 5px solid #0284c7; font-size: .92rem; box-shadow: 0 1px 3px rgba(2, 132, 199, .15); }
    .fa-flujo.venta { background: linear-gradient(90deg, #dcfce7, #f0fdf4); border-color: #86efac; border-left-color: #16a34a; box-shadow: 0 1px 3px rgba(22, 163, 74, .15); }
    .fa-flujo .tipo { padding: 2px 10px; border-radius: 999px; background: #0284c7; color: #fff; font-weight: 800; font-size: .8rem; } .fa-flujo.venta .tipo { background: #16a34a; }
    .fa-flujo b { color: #0f172a; } .fa-flujo .fl { color: #0284c7; font-weight: 800; font-size: 1.1rem; } .fa-flujo.venta .fl { color: #16a34a; }

    /* Xubio */
    .xb { font-family: "Open Sans", "Segoe UI", Arial, sans-serif; color: #333; background: #fff; border: 1px solid #ddd; border-top: 3px solid #4aa3df; padding: 18px 22px 0; }
    .xb-cab { display: grid; grid-template-columns: max-content minmax(0, 210px) max-content minmax(0, 210px); gap: 9px 18px; align-items: center; }
    @media (max-width: 760px) { .xb-cab { grid-template-columns: max-content minmax(0, 1fr); } }
    .xb label, .xb .xb-l { font-size: .95rem; white-space: nowrap; }
    .xb-q { display: inline-grid; place-items: center; width: 13px; height: 13px; margin-left: 4px; border-radius: 50%; background: #bbb; color: #fff; font-size: 9px; font-weight: 700; vertical-align: 1px; }
    .xb-in { width: 100%; min-width: 0; height: 26px; padding: 2px 6px; border: 1px solid #a9a9a9; border-radius: 0; font: inherit; font-size: .88rem; color: #333; background: #fff; }
    .xb-in:focus { outline: 1px solid #4aa3df; border-color: #4aa3df; }
    .xb-in.num { text-align: right; font-variant-numeric: tabular-nums; }
    .xb-in.error { border-color: #dc2626; background: #fef2f2; }
    .xb-in.aviso { border-color: #f59e0b; background: #fffbeb; }
    .xb-cuit { display: block; margin-top: 2px; font-size: .72rem; color: #777; }
    .xb-mas { margin: 14px 0 0; }
    .xb-mas summary, .xb-perc summary { color: #00618a; cursor: pointer; font-size: .92rem; list-style: none; }
    .xb-mas summary::before, .xb-perc summary::before { content: "▶"; color: #4aa3df; font-size: .7rem; margin-right: 6px; }
    .xb-mas[open] summary::before, .xb-perc[open] summary::before { content: "▼"; }
    .xb-mas .xb-cab { margin-top: 10px; }
    .xb-items { margin-top: 18px; overflow-x: auto; }
    .xb-tabla { width: 100%; min-width: 980px; border-collapse: collapse; font-size: .86rem; }
    .xb-tabla th { background: #eee; font-weight: 700; padding: 5px 6px; text-align: left; white-space: nowrap; }
    .xb-tabla th.n { text-align: right; }
    .xb-tabla td { padding: 6px; border-bottom: 1px solid #ddd; }
    .xb-tabla td.gris { color: #aaa; text-align: right; font-variant-numeric: tabular-nums; }
    .xb-x { border: 0; background: none; color: #aaa; cursor: pointer; font-size: 1rem; }
    .xb-x:hover { color: #c00; }
    .xb-boton { margin: 16px 0 0; padding: 6px 18px; border: 1px solid #c5c5c5; border-radius: 2px; background: linear-gradient(#fafafa, #e6e6e6); color: #00618a; font: inherit; font-weight: 600; cursor: pointer; }
    .xb-prod { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 14px; font-size: .82rem; color: #555; }
    .xb-prod button { padding: 3px 10px; border: 1px solid #b6d7ea; border-radius: 999px; background: #f2f9fd; color: #00618a; font: inherit; font-size: .8rem; cursor: pointer; }
    .xb-prod button:hover { background: #e1f1fa; }
    .xb-perc { margin: 22px 0 0; }
    .xb-perc .xb-cab { margin-top: 10px; }
    .xb-tot { display: flex; flex-wrap: wrap; gap: 6px 40px; margin-top: 26px; padding: 16px 0; border-top: 1px solid #e5e5e5; color: #00618a; font-size: 1rem; }
    .xb-tot span b { display: inline-block; min-width: 110px; margin-left: 24px; text-align: right; font-variant-numeric: tabular-nums; }
    .xb-pie { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 10px; padding: 16px 0 18px; border-top: 3px solid #e5e5e5; }
    .xb-pie button { padding: 8px 22px; border: 1px solid #3c3c3c; border-radius: 2px; background: #fff; color: #00618a; font: inherit; font-weight: 600; cursor: pointer; }
    .xb-pie .guardar { border-color: #00618a; background: linear-gradient(#0a8fbf, #00618a); color: #fff; }
    .xb-pie button:disabled { opacity: .6; cursor: progress; }

    /* ARCA */
    .ar { font-family: Arial, Helvetica, sans-serif; color: #000; background: #fff; border: 2px solid #000; font-size: .85rem; }
    .ar-orig { text-align: center; font-weight: 700; padding: 4px; border-bottom: 2px solid #000; letter-spacing: .1em; }
    .ar-cab { display: grid; grid-template-columns: 1fr 80px 1fr; border-bottom: 2px solid #000; }
    .ar-cab > div { padding: 10px 12px; }
    .ar-letra { display: flex; flex-direction: column; align-items: center; justify-content: flex-start; border-left: 2px solid #000; border-right: 2px solid #000; padding: 4px 0 !important; }
    .ar-letra b { font-size: 2.4rem; line-height: 1; }
    .ar-letra small { font-size: .65rem; font-weight: 700; }
    .ar-razon { font-size: 1.1rem; font-weight: 700; margin-bottom: 6px; }
    .ar-tipo { font-size: 1.3rem; font-weight: 700; margin-bottom: 6px; }
    .ar p { margin: 2px 0; }
    .ar-rec { padding: 8px 12px; border-bottom: 2px solid #000; display: grid; grid-template-columns: 1fr 1fr; gap: 2px 16px; }
    .ar-tabla { width: 100%; border-collapse: collapse; }
    .ar-tabla th { background: #ccc; border: 1px solid #000; padding: 3px 5px; font-size: .78rem; }
    .ar-tabla td { padding: 3px 5px; font-variant-numeric: tabular-nums; }
    .ar-tabla .n { text-align: right; }
    .ar-tot { margin: 10px 12px 12px auto; width: min(340px, 100%); border: 1px solid #000; padding: 6px 10px; }
    .ar-tot div { display: flex; justify-content: space-between; gap: 12px; padding: 1px 0; }
    .ar-tot div:last-child { font-weight: 700; border-top: 1px solid #000; margin-top: 3px; padding-top: 3px; }
  `,
  template: `
    @let l = lectura();
    @let f = estudio();
    <div class="fa-barra">
      <div class="fa-ver" role="group" aria-label="Ver la factura como">
        <button type="button" [attr.aria-pressed]="modo() === 'xubio'" (click)="modo.set('xubio')">Como en Xubio</button>
        <button type="button" [attr.aria-pressed]="modo() === 'arca'" (click)="modo.set('arca')">Como en ARCA</button>
      </div>
      @if (f) {
        @let venta = f.operacion === 'Ventas';
        <div class="fa-flujo" [class.venta]="venta" [title]="sesion() ? 'Estudio en Xubio: ' + sesion() : ''">
          <span class="tipo">{{ venta ? '↗ Venta' : '↘ Compra' }}</span>
          <b>{{ venta ? f.empresa : (f.contraparte.nombre || 'proveedor sin nombre') }}</b>
          <span class="fl" aria-hidden="true">⟶</span>
          <b>{{ venta ? (f.contraparte.nombre || 'cliente sin nombre') : f.empresa }}</b>
        </div>
      }
    </div>

    @if (modo() === 'xubio') {
      <div class="xb">
        <div class="xb-cab">
          <label [for]="id + '-cp'">{{ contraparte() }} *<span class="xb-q">?</span></label>
          <span>
            <input class="xb-in" [id]="id + '-cp'" [class]="nivel()(lado())" [value]="l.encabezado[lado()] ?? ''" (change)="encabezado.emit([lado(), val($event)])" />
            <input class="xb-in mt-1" [class]="nivel()(lado() + '_cuit')" [value]="l.encabezado[lado() + '_cuit'] ?? ''" placeholder="CUIT" [attr.aria-label]="'CUIT del ' + contraparte().toLowerCase()" (change)="encabezado.emit([lado() + '_cuit', val($event)])" />
          </span>
          <label [for]="id + '-cond'">Condición de Pago *<span class="xb-q">?</span></label>
          <select class="xb-in" [id]="id + '-cond'" (change)="extra('condicion', val($event))">
            @for (c of condiciones; track c) { <option [selected]="extras().condicion === c">{{ c }}</option> }
          </select>
          <label [for]="id + '-tipo'">Comprobante<span class="xb-q">?</span></label>
          <input class="xb-in" [id]="id + '-tipo'" [class]="nivel()('tipo')" [value]="l.encabezado['tipo'] ?? ''" (change)="encabezado.emit(['tipo', val($event)])" />
          <label [for]="id + '-num'">Número *<span class="xb-q">?</span></label>
          <input class="xb-in" [id]="id + '-num'" [class]="nivel()('numero')" [value]="numeroXubio()" (change)="encabezado.emit(['numero', val($event)])" />
          <label [for]="id + '-fecha'">Fecha Comprobante *<span class="xb-q">?</span></label>
          <input class="xb-in" [id]="id + '-fecha'" [class]="nivel()('fecha')" [value]="l.encabezado['fecha'] ?? ''" placeholder="DD/MM/AAAA" (change)="encabezado.emit(['fecha', val($event)])" />
          <label [for]="id + '-venc'">Vencimiento del Pago<span class="xb-q">?</span></label>
          <input class="xb-in" [id]="id + '-venc'" [value]="extras().vencimiento || l.encabezado['fecha'] || ''" placeholder="DD/MM/AAAA" (change)="extra('vencimiento', val($event))" />
        </div>

        <details class="xb-mas">
          <summary>Más Opciones</summary>
          <div class="xb-cab">
            <label [for]="id + '-emp'">{{ operacion() === 'Ventas' ? 'Emisor (la empresa)' : 'Receptor (la empresa)' }}</label>
            <input class="xb-in" [id]="id + '-emp'" [value]="l.encabezado[ladoEmpresa()] ?? ''" (change)="encabezado.emit([ladoEmpresa(), val($event)])" />
            <label [for]="id + '-empc'">CUIT de la empresa</label>
            <input class="xb-in" [id]="id + '-empc'" [class]="nivel()(ladoEmpresa() + '_cuit')" [value]="l.encabezado[ladoEmpresa() + '_cuit'] ?? ''" (change)="encabezado.emit([ladoEmpresa() + '_cuit', val($event)])" />
          </div>
        </details>

        @if (productos().length) {
          <div class="xb-prod" aria-label="Productos sugeridos">
            <span>Productos:</span>
            @for (p of productos(); track p) { <button type="button" (click)="usarProducto(p)">+ {{ p }}</button> }
          </div>
        }

        <div class="xb-items">
          <table class="xb-tabla">
            <thead>
              <tr>
                <th style="width: 18px"></th><th>Producto/Servicio<span class="xb-q">?</span></th><th>Centro de Costo<span class="xb-q">?</span></th><th>Observaciones<span class="xb-q">?</span></th>
                <th class="n">Cantidad<span class="xb-q">?</span></th><th class="n">Precio<span class="xb-q">?</span></th><th class="n">% Dto.<span class="xb-q">?</span></th>
                <th class="n">Importe<span class="xb-q">?</span></th><th class="n">IVA<span class="xb-q">?</span></th><th class="n" title="Impuestos internos del ítem">Imp. Int.</th><th class="n">Total<span class="xb-q">?</span></th>
              </tr>
            </thead>
            <tbody>
              @for (x of l.items; track $index; let i = $index) {
                <tr>
                  <td><button type="button" class="xb-x" (click)="sacar.emit(i)" [attr.aria-label]="'Sacar el ítem ' + (i + 1)">✕</button></td>
                  <td style="width: 150px">
                    <input class="xb-in" [value]="extras().productos[i] ?? ''" [attr.list]="id + '-prods'" placeholder="Elegí o escribí" (change)="ponerProducto(i, val($event))" aria-label="Producto o servicio" />
                  </td>
                  <td style="width: 130px"><input class="xb-in" [value]="extras().centros[i] ?? ''" (change)="lista('centros', i, val($event))" aria-label="Centro de costo" /></td>
                  <td><input class="xb-in" [value]="x.descripcion" (change)="item.emit([i, 'descripcion', val($event)])" aria-label="Observaciones" /></td>
                  <td style="width: 80px"><input class="xb-in num" [value]="dec(x.cantidad)" (change)="item.emit([i, 'cantidad', val($event)])" aria-label="Cantidad" /></td>
                  <td style="width: 105px"><input class="xb-in num" [value]="dec(x.precio)" (change)="item.emit([i, 'precio', val($event)])" aria-label="Precio" /></td>
                  <td style="width: 64px"><input class="xb-in num" [value]="dec(extras().dto[i] ?? 0)" (change)="lista('dto', i, val($event))" aria-label="Descuento" /></td>
                  <td style="width: 105px"><input class="xb-in num" [value]="dec(x.importe)" (change)="item.emit([i, 'importe', val($event)])" aria-label="Importe" /></td>
                  <td style="width: 120px">
                    <select class="xb-in" (change)="item.emit([i, 'alicuota', val($event)])" aria-label="Alícuota de IVA">
                      @for (a of alicuotas; track a) { <option [value]="a" [selected]="x.alicuota === a">{{ a }}% · {{ dec(ivaDe(x, a)) }}</option> }
                      @if (x.alicuota !== null && !alicuotas.includes(x.alicuota)) { <option selected>{{ x.alicuota }}%</option> }
                    </select>
                  </td>
                  <td style="width: 90px"><input class="xb-in num" [value]="dec(x.impuestos_internos)" placeholder="0,00" (change)="item.emit([i, 'impuestos_internos', val($event)])" aria-label="Impuestos internos del ítem" /></td>
                  <td class="gris" style="width: 95px">{{ dec(totalDe(x)) }}</td>
                </tr>
              }
            </tbody>
          </table>
          <datalist [id]="id + '-prods'">@for (p of productos(); track p) { <option [value]="p"></option> }</datalist>
        </div>
        <button type="button" class="xb-boton" (click)="agregar.emit()">Agregar Item</button>

        <details class="xb-perc">
          <summary>Percepciones e Impuestos</summary>
          <div class="xb-cab">
            @for (c of totales; track c.id) {
              <label [for]="id + '-t-' + c.id">{{ c.etiqueta }}</label>
              <input class="xb-in num" [id]="id + '-t-' + c.id" [class]="nivel()(c.id)" [value]="dec(l.totales[c.id])" (change)="total.emit([c.id, val($event)])" />
            }
          </div>
        </details>

        <div class="xb-tot">
          <span>Total Bruto <b>{{ dec(bruto()) }}</b></span>
          <span>Total Impuestos <b>{{ dec(impuestos()) }}</b></span>
          <span>Total <b>{{ dec(l.totales['total']) }}</b></span>
        </div>
        <div class="xb-pie">
          <button type="button" class="guardar" (click)="guardar.emit(extras())" [disabled]="ocupado()" title="Genera el Excel con lo que ves">{{ ocupado() ? 'Armando…' : 'Guardar' }}</button>
          <button type="button" (click)="eliminar.emit()" title="Saca este archivo de la lista">Eliminar</button>
          <button type="button" (click)="cancelar.emit()" title="Vuelve arriba sin generar">Cancelar</button>
        </div>
      </div>
    } @else {
      <div class="ar" aria-label="La factura como en ARCA">
        <div class="ar-orig">ORIGINAL</div>
        <div class="ar-cab">
          <div>
            <div class="ar-razon">{{ l.encabezado['emisor'] || 'Emisor sin nombre' }}</div>
            <p><b>CUIT:</b> {{ cuit(l.encabezado['emisor_cuit']) }}</p>
          </div>
          <div class="ar-letra"><b>{{ letra() }}</b><small>COD. {{ codigo() }}</small></div>
          <div>
            <div class="ar-tipo">{{ nombreTipo() }}</div>
            <p><b>Punto de Venta:</b> {{ puntoVenta() }} &nbsp; <b>Comp. Nro:</b> {{ numeroComp() }}</p>
            <p><b>Fecha de Emisión:</b> {{ l.encabezado['fecha'] || '—' }}</p>
          </div>
        </div>
        <div class="ar-rec">
          <p><b>CUIT:</b> {{ cuit(l.encabezado['receptor_cuit']) }}</p>
          <p><b>Apellido y Nombre / Razón Social:</b> {{ l.encabezado['receptor'] || '—' }}</p>
          <p><b>Condición de venta:</b> {{ extras().condicion }}</p>
        </div>
        <table class="ar-tabla">
          <thead><tr><th>Producto / Servicio</th><th>Cantidad</th><th>Precio Unit.</th><th>Alícuota IVA</th><th>Subtotal</th></tr></thead>
          <tbody>
            @for (x of l.items; track $index; let i = $index) {
              <tr><td>{{ extras().productos[i] ? extras().productos[i] + ' — ' : '' }}{{ x.descripcion }}</td><td class="n">{{ dec(x.cantidad) }}</td><td class="n">{{ dec(x.precio) }}</td><td class="n">{{ x.alicuota ?? '—' }}{{ x.alicuota !== null ? '%' : '' }}</td><td class="n">{{ dec(x.importe) }}</td></tr>
            } @empty {
              <tr><td colspan="5">Sin detalle leído.</td></tr>
            }
          </tbody>
        </table>
        <div class="ar-tot">
          <div><span>Importe Neto Gravado: $</span><span>{{ dec(l.totales['neto_gravado']) }}</span></div>
          @if (l.totales['no_gravado']) { <div><span>Importe Neto No Gravado: $</span><span>{{ dec(l.totales['no_gravado']) }}</span></div> }
          @if (l.totales['exento']) { <div><span>Importe Exento: $</span><span>{{ dec(l.totales['exento']) }}</span></div> }
          <div><span>IVA: $</span><span>{{ dec(l.totales['iva']) }}</span></div>
          @if (l.totales['impuestos_internos']) { <div><span>Impuestos Internos: $</span><span>{{ dec(l.totales['impuestos_internos']) }}</span></div> }
          @if (l.totales['percepcion_iibb']) { <div><span>Percepción IIBB Bs. As.: $</span><span>{{ dec(l.totales['percepcion_iibb']) }}</span></div> }
          <div><span>Importe Otros Tributos: $</span><span>{{ dec(l.totales['otros_tributos'] ?? 0) }}</span></div>
          <div><span>Importe Total: $</span><span>{{ dec(l.totales['total']) }}</span></div>
        </div>
      </div>
    }
  `,
})
export class Factura {
  readonly lectura = input.required<LecturaExcel>();
  readonly estudio = input<FacturaDelEstudio | null>(null);
  /** Usuario de Xubio guardado (el estudio). */
  readonly sesion = input<string>('');
  readonly nivel = input<(campo: string) => string>(() => '');
  readonly ocupado = input(false);

  readonly encabezado = output<[string, string]>();
  readonly total = output<[string, string]>();
  readonly item = output<[number, keyof ItemLeido, string]>();
  readonly agregar = output<void>();
  readonly sacar = output<number>();
  readonly guardar = output<Extras>();
  readonly eliminar = output<void>();
  readonly cancelar = output<void>();

  protected readonly id = 'fa' + Math.random().toString(36).slice(2, 8);
  protected readonly modo = signal<'xubio' | 'arca'>('xubio');
  protected readonly alicuotas = ALICUOTAS;
  protected readonly condiciones = ['Cuenta Corriente', 'Contado', 'Tarjeta de crédito', 'Transferencia'];
  protected readonly totales = [
    { id: 'neto_gravado', etiqueta: 'Neto gravado' },
    { id: 'iva', etiqueta: 'IVA' },
    { id: 'no_gravado', etiqueta: 'No gravado' },
    { id: 'exento', etiqueta: 'Exento' },
    { id: 'impuestos_internos', etiqueta: 'Impuestos internos' },
    { id: 'percepcion_iibb', etiqueta: 'Percepción IIBB Bs. As.' },
    { id: 'otros_tributos', etiqueta: 'Otras percepciones / tributos' },
    { id: 'total', etiqueta: 'Total' },
  ];
  protected readonly cuit = (c: string | undefined) => (c ? cuitConGuiones(c) : '—');
  protected readonly dec = (n: number | null | undefined) => (n === null || n === undefined ? '' : aTexto(n));

  /** Lo de pantalla, por archivo. */
  private readonly porArchivo = signal<Record<string, Extras>>({});
  /** Para que las sugerencias se actualicen al recordar un producto. */
  private readonly recordados = signal(0);

  protected readonly operacion = computed(() => this.estudio()?.operacion ?? 'Compras');
  /** La contraparte: el emisor en una compra, el receptor en una venta. */
  protected readonly lado = computed(() => (this.operacion() === 'Ventas' ? 'receptor' : 'emisor'));
  protected readonly ladoEmpresa = computed(() => (this.operacion() === 'Ventas' ? 'emisor' : 'receptor'));
  protected readonly contraparte = computed(() => (this.operacion() === 'Ventas' ? 'Cliente' : 'Proveedor'));
  protected readonly extras = computed<Extras>(
    () => this.porArchivo()[this.lectura().archivo] ?? { condicion: 'Cuenta Corriente', vencimiento: '', productos: [], centros: [], dto: [] },
  );
  protected readonly productos = computed(() => {
    this.recordados();
    return productosSugeridos(this.estudio()?.empresa ?? '', this.operacion());
  });

  protected readonly bruto = computed(() => {
    const t = this.lectura().totales;
    return (t['neto_gravado'] ?? 0) + (t['no_gravado'] ?? 0) + (t['exento'] ?? 0);
  });
  protected readonly impuestos = computed(() => {
    const t = this.lectura().totales;
    return (t['iva'] ?? 0) + (t['impuestos_internos'] ?? 0) + (t['percepcion_iibb'] ?? 0) + (t['otros_tributos'] ?? 0);
  });

  // «1 - Factura A» / «81 - Tique Factura A»: letra, código y nombre como en ARCA.
  private readonly tipo = computed(() => this.lectura().encabezado['tipo'] ?? '');
  protected readonly codigo = computed(() => (this.tipo().match(/^\s*(\d{1,3})/)?.[1] ?? '').padStart(3, '0') || '—');
  protected readonly letra = computed(() => this.tipo().match(/\b([ABCME])\s*$/)?.[1] ?? '—');
  protected readonly nombreTipo = computed(() => (this.tipo().replace(/^\s*\d+\s*-\s*/, '').replace(/\s+[ABCME]\s*$/, '') || 'COMPROBANTE').toUpperCase());
  protected readonly puntoVenta = computed(() => this.numeroPartes()[0]);
  protected readonly numeroComp = computed(() => this.numeroPartes()[1]);
  /** En Xubio el número va con la letra: «A-00016-00004113». */
  protected readonly numeroXubio = computed(() => {
    const n = this.lectura().encabezado['numero'] ?? '';
    return n && this.letra() !== '—' && !/^[A-Z]-/.test(n) ? `${this.letra()}-${n}` : n;
  });
  private readonly numeroPartes = computed(() => {
    const m = (this.lectura().encabezado['numero'] ?? '').match(/(\d{1,5})\D+(\d{1,8})/);
    return m ? [m[1].padStart(5, '0'), m[2].padStart(8, '0')] : ['—', '—'];
  });

  constructor() {
    // Al cambiar de archivo se vuelve a la vista de Xubio.
    effect(() => {
      this.lectura().archivo;
      this.modo.set('xubio');
    });
  }

  protected val(e: Event): string {
    return (e.target as HTMLInputElement).value;
  }

  protected ivaDe(x: ItemLeido, a: number): number {
    return Math.round((x.importe ?? 0) * a) / 100;
  }

  protected totalDe(x: ItemLeido): number {
    return (x.importe ?? 0) + this.ivaDe(x, x.alicuota ?? 0) + (x.impuestos_internos ?? 0);
  }

  protected extra(campo: 'condicion' | 'vencimiento', valor: string): void {
    this.cambiar((e) => ({ ...e, [campo]: valor }));
  }

  protected lista(campo: 'centros' | 'dto', i: number, valor: string): void {
    this.cambiar((e) => {
      const l = [...e[campo]] as (string | number)[];
      l[i] = campo === 'dto' ? Number(valor.replace(',', '.')) || 0 : valor;
      return { ...e, [campo]: l };
    });
  }

  protected ponerProducto(i: number, nombre: string): void {
    this.cambiar((e) => {
      const productos = [...e.productos];
      productos[i] = nombre.trim();
      return { ...e, productos };
    });
    const empresa = this.estudio()?.empresa;
    if (empresa && nombre.trim()) {
      recordarProducto(empresa, nombre.trim(), this.operacion());
      this.recordados.update((n) => n + 1);
    }
  }

  /** Botón de producto: va al primer ítem sin producto; si no hay, agrega uno. */
  protected usarProducto(nombre: string): void {
    const libre = this.lectura().items.findIndex((_, i) => !this.extras().productos[i]);
    if (libre >= 0) {
      this.ponerProducto(libre, nombre);
      return;
    }
    const nuevo = this.lectura().items.length;
    this.agregar.emit();
    this.ponerProducto(nuevo, nombre);
  }

  private cambiar(f: (e: Extras) => Extras): void {
    const archivo = this.lectura().archivo;
    this.porArchivo.update((m) => ({ ...m, [archivo]: f(this.extras()) }));
  }
}
