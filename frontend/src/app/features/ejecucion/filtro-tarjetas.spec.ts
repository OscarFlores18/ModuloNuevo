import Swal from 'sweetalert2';
import { Advertencia, ResumenOperacion } from '../../core/models';
import { mostrarVentanaDeErrores } from './ventana-errores';

type Op = 'Ventas' | 'Compras';
const fila = (pares: [string, string | number][]) => pares.map(([columna, valor]) => ({ columna, valor }));
const comprobante = (origen: 'arca' | 'xubio', nro: string, total = 100) => ({
  origen,
  fila: fila([['Fecha', '01/11/2026'], ['Tipo', 'Factura A'], ['Comprobante', nro], ['CUIT', '30707310657'], ['Razón social', 'NOVIT'], ['Total', total]]),
});
const hallazgo = (cliente: string, tipo: string, operacion: Op | null, comprobantes: unknown[], total: number, gravedad = 'corregir') =>
  ({ tipo, gravedad, cliente, operacion, titulo: 'Hallazgo', detalle: '', acciones: [], total, comprobantes, hoja: null }) as unknown as Advertencia;
const resumen = (cliente: string, operacion: Op): ResumenOperacion => ({ cliente, operacion, arca: 10, xubio: 10, coinciden: 8, a_corregir: 0 });

const abrir = async (errores: Advertencia[], resumenes: ResumenOperacion[], clienteInicial?: string) => {
  void mostrarVentanaDeErrores(errores, resumenes, true, undefined, undefined, undefined, undefined, undefined, clienteInicial);
  await new Promise((r) => setTimeout(r, 0));
  const popup = Swal.getPopup()!;
  return {
    popup,
    chip: (ver: string) => popup.querySelector<HTMLButtonElement>(`button[data-ver="${ver}"]`)!,
    num: (ver: string) => popup.querySelector(`button[data-ver="${ver}"] .ve-chip-num`)!.textContent,
    grupo: (cliente: string) => popup.querySelector<HTMLElement>(`.ve-grupo[data-cliente="${cliente}"]`)!,
    tarjeta: (cliente: string) => popup.querySelector<HTMLElement>(`[data-tarjeta="${cliente}"]`)!,
    guia: () => popup.querySelector<HTMLElement>('[data-guia]')!,
    barra: () => popup.querySelector<HTMLElement>('.ve-barra')!,
  };
};

describe('Tarjetas por cliente y filtros de la ventana de hallazgos', () => {
  afterEach(() => Swal.close());

  const errores = [
    hallazgo('Alfa', 'faltante_en_xubio', 'Compras', [comprobante('arca', 'A-0001-00000001')], 1),
    hallazgo('Alfa', 'faltante_en_arca', 'Ventas', [comprobante('xubio', 'A-0002-00000001'), comprobante('xubio', 'A-0002-00000002')], 2),
    hallazgo('Beta', 'importe_distinto', 'Ventas', [comprobante('arca', 'A-0003-00000001', 100), comprobante('xubio', 'A-0003-00000001', 90)], 1),
    // Un error grave suelto (un paso que falló): cuenta en «Error».
    hallazgo('Beta', 'arca_vacio', 'Compras', [], 0, 'grave'),
  ];
  const resumenes = [resumen('Alfa', 'Ventas'), resumen('Alfa', 'Compras'), resumen('Beta', 'Ventas')];

  it('sin cliente elegido: tarjetas normales, guía y ni filtros ni lista', async () => {
    const v = await abrir(errores, resumenes);
    expect(v.popup.querySelectorAll('.ve-kpi')).toHaveLength(2);
    expect(v.tarjeta('Alfa').querySelectorAll('.ve-kpi-mod')).toHaveLength(2);
    expect(v.tarjeta('Alfa').textContent).toContain('8 de 10 comprobantes de ARCA coinciden con Xubio');
    expect(v.popup.querySelector('.ve-kpi-barra')).toBeNull();
    expect([...v.popup.querySelectorAll('[data-tarjeta]')].every((t) => t.getAttribute('aria-pressed') === 'false')).toBe(true);
    expect(v.popup.querySelector('.ve-kpis')!.hasAttribute('data-filtrando')).toBe(false);
    expect(v.guia().hidden).toBe(false);
    expect(v.barra().hidden).toBe(true);
    expect(v.grupo('Alfa').hidden && v.grupo('Beta').hidden).toBe(true);
  });

  it('elegir un cliente muestra sus filtros y su lista; los graves cuentan en «Error», que se elige solo', async () => {
    const v = await abrir(errores, resumenes);

    v.tarjeta('Beta').click();
    expect(v.guia().hidden).toBe(true);
    expect(v.barra().hidden).toBe(false);
    expect(v.grupo('Beta').hidden).toBe(false);
    expect(v.grupo('Alfa').hidden).toBe(true);
    // 1 importe distinto + 1 grave suelto; «Todos» es la suma.
    expect([v.num(''), v.num('error')]).toEqual(['2', '2']);
    expect(v.chip('error').hidden).toBe(false);
    expect(v.chip('error').getAttribute('aria-pressed')).toBe('true');
    expect(v.grupo('Beta').querySelector<HTMLElement>('details[data-suelta="error"]')!.hidden).toBe(false);

    // Alfa no tiene errores: «Error» desaparece y vuelve a «Todos».
    v.tarjeta('Alfa').click();
    expect(v.chip('error').hidden).toBe(true);
    expect(v.chip('').getAttribute('aria-pressed')).toBe('true');
    expect([v.num(''), v.num('subir'), v.num('revisar')]).toEqual(['3', '1', '2']);
    v.chip('subir').click();
    expect(v.grupo('Alfa').querySelector<HTMLElement>('.ve-bloque[data-bloque="revisar"]')!.hidden).toBe(true);

    // Otro clic en la misma: sin elegir, vuelve la guía.
    v.tarjeta('Alfa').click();
    expect(v.guia().hidden).toBe(false);
    expect(v.barra().hidden).toBe(true);
    expect(v.grupo('Alfa').hidden).toBe(true);
  });

  it('abre con el cliente pedido ya elegido', async () => {
    const v = await abrir(errores, resumenes, 'Beta');
    expect(v.tarjeta('Beta').getAttribute('aria-pressed')).toBe('true');
    expect(v.chip('error').getAttribute('aria-pressed')).toBe('true');
    expect(v.guia().hidden).toBe(true);
  });

  it('sin tarjetas (un solo hallazgo) se ve todo, sin guía', async () => {
    const v = await abrir([errores[0]], []);
    expect(v.popup.querySelector('[data-guia]')).toBeNull();
    expect(v.grupo('Alfa').hidden).toBe(false);
  });
});
