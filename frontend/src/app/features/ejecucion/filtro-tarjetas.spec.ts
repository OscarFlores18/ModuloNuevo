import Swal from 'sweetalert2';
import { Advertencia, ResumenOperacion } from '../../core/models';
import { mostrarVentanaDeErrores } from './ventana-errores';

type Op = 'Ventas' | 'Compras';
const fila = (pares: [string, string | number][]) => pares.map(([columna, valor]) => ({ columna, valor }));
const comprobante = (origen: 'arca' | 'xubio', nro: string, total = 100) => ({
  origen,
  fila: fila([['Fecha', '01/11/2026'], ['Tipo', 'Factura A'], ['Comprobante', nro], ['CUIT', '30707310657'], ['Razón social', 'NOVIT'], ['Total', total]]),
});
const hallazgo = (cliente: string, tipo: string, operacion: Op, comprobantes: unknown[], total: number) =>
  ({ tipo, gravedad: 'corregir', cliente, operacion, titulo: '', detalle: '', acciones: [], total, comprobantes, hoja: null }) as unknown as Advertencia;
const resumen = (cliente: string, operacion: Op): ResumenOperacion => ({ cliente, operacion, arca: 10, xubio: 10, coinciden: 8, a_corregir: 0 });

describe('Tarjetas por cliente y filtros de la ventana de hallazgos', () => {
  afterEach(() => Swal.close());

  it('una tarjeta por cliente; «Error» primero si hay; cliente, operación y estado combinados', async () => {
    void mostrarVentanaDeErrores(
      [
        hallazgo('Alfa', 'faltante_en_xubio', 'Compras', [comprobante('arca', 'A-0001-00000001')], 1),
        hallazgo('Alfa', 'faltante_en_arca', 'Ventas', [comprobante('xubio', 'A-0002-00000001'), comprobante('xubio', 'A-0002-00000002')], 2),
        hallazgo('Beta', 'importe_distinto', 'Ventas', [comprobante('arca', 'A-0003-00000001', 100), comprobante('xubio', 'A-0003-00000001', 90)], 1),
      ],
      [resumen('Alfa', 'Ventas'), resumen('Alfa', 'Compras'), resumen('Beta', 'Ventas')],
    );
    await new Promise((r) => setTimeout(r, 0));
    const popup = Swal.getPopup()!;
    const chip = (ver: string) => popup.querySelector<HTMLButtonElement>(`button[data-ver="${ver}"]`)!;
    const chipOp = (op: string) => popup.querySelector<HTMLButtonElement>(`button[data-op-filtro="${op}"]`)!;
    const num = (b: HTMLElement) => b.querySelector('.ve-chip-num')!.textContent;
    const grupo = (cliente: string) => popup.querySelector<HTMLElement>(`.ve-grupo[data-cliente="${cliente}"]`)!;
    const bloque = (cliente: string, id: string) => grupo(cliente).querySelector<HTMLElement>(`.ve-bloque[data-bloque="${id}"]`)!;
    const tarjeta = (cliente: string) => popup.querySelector<HTMLElement>(`[data-tarjeta="${cliente}"]`)!;
    const grupoOp = popup.querySelector<HTMLElement>('[data-filtros-op]')!;

    // Una tarjeta por cliente, con sus módulos adentro.
    expect(popup.querySelectorAll('.ve-kpi')).toHaveLength(2);
    expect(tarjeta('Alfa').querySelectorAll('.ve-kpi-mod')).toHaveLength(2);
    expect(tarjeta('Beta').querySelectorAll('.ve-kpi-mod')).toHaveLength(1);

    // Al abrir: hay un error, así que «Error» queda elegido y solo se ve Beta.
    expect(chip('error').hidden).toBe(false);
    expect(chip('error').getAttribute('aria-pressed')).toBe('true');
    expect(grupo('Alfa').hidden).toBe(true);
    expect(grupo('Beta').hidden).toBe(false);
    expect(grupoOp.hidden).toBe(false);
    expect([num(chipOp('')), num(chipOp('Ventas')), num(chipOp('Compras'))]).toEqual(['4', '3', '1']);

    // Alfa no tiene errores: «Error» desaparece y vuelve a «Todos».
    tarjeta('Alfa').click();
    expect(tarjeta('Alfa').getAttribute('aria-pressed')).toBe('true');
    expect(chip('error').hidden).toBe(true);
    expect(chip('').getAttribute('aria-pressed')).toBe('true');
    expect(num(chip(''))).toBe('3');
    expect(grupo('Beta').hidden).toBe(true);
    expect(grupo('Alfa').hidden).toBe(false);

    // Operación Compras dentro de Alfa: solo «Subir a Xubio».
    chipOp('Compras').click();
    expect([num(chip('')), num(chip('subir')), num(chip('revisar'))]).toEqual(['1', '1', '0']);
    expect(chip('revisar').disabled).toBe(true);
    expect(bloque('Alfa', 'subir').hidden).toBe(false);
    expect(bloque('Alfa', 'revisar').hidden).toBe(true);

    // Beta tiene errores y una sola operación: «Error» elegido y sin filtro de operación.
    tarjeta('Beta').click();
    expect(chip('error').getAttribute('aria-pressed')).toBe('true');
    expect(grupoOp.hidden).toBe(true);
    expect(grupo('Alfa').hidden).toBe(true);

    // Otro clic en Beta: todos los clientes otra vez.
    tarjeta('Beta').click();
    expect(popup.querySelector('.ve-kpis')!.hasAttribute('data-filtrando')).toBe(false);
    expect(popup.querySelector<HTMLElement>('[data-filtro-tarjeta]')!.hidden).toBe(true);
    chip('').click();
    expect(grupo('Alfa').hidden).toBe(false);
    expect(grupo('Beta').hidden).toBe(false);
  });
});
