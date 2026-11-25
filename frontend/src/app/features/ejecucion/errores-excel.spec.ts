import Swal from 'sweetalert2';
import { Advertencia } from '../../core/models';
import { ContextoVentana, mostrarVentanaDeErrores } from './ventana-errores';

type Op = 'Ventas' | 'Compras';
const fila = (pares: [string, string | number][]) => pares.map(([columna, valor]) => ({ columna, valor }));
const delLibro = (nro: string) => ({
  origen: 'xubio',
  fila: fila([['Fecha', '01/11/2026'], ['Tipo', 'Factura A'], ['Comprobante', nro], ['CUIT', '30707310657'], ['Razón social', 'NOVIT'], ['Total', 100]]),
});
const sinArca = (cliente: string, operacion: Op, nros: string[]) =>
  ({
    tipo: 'faltante_en_arca', gravedad: 'corregir', cliente, operacion, titulo: 'Hallazgo', detalle: '', acciones: [],
    total: nros.length, comprobantes: nros.map(delLibro), hoja: null,
  }) as unknown as Advertencia;

const esperar = () => new Promise((r) => setTimeout(r, 0));

async function abrir(errores: Advertencia[], descargar?: ContextoVentana['descargarErroresExcel'], canEdit = true) {
  const ctx: ContextoVentana = { emails: { Acme: 'acme@ejemplo.com' }, periodo: '01/11/2026 al 30/11/2026', descargarErroresExcel: descargar };
  void mostrarVentanaDeErrores(errores, [], canEdit, undefined, 'ej1', undefined, undefined, ctx);
  await esperar();
  const popup = Swal.getPopup()!;
  const bloque = popup.querySelector<HTMLElement>('.ve-bloque[data-bloque="revisar"]')!;
  return {
    popup,
    bloque,
    excel: (op: string) => bloque.querySelector<HTMLButtonElement>(`button.ve-boton-excel[data-op="${op}"]`)!,
    excels: () => [...bloque.querySelectorAll<HTMLButtonElement>('button.ve-boton-excel')],
  };
}

const aviso = () => document.querySelector<HTMLElement>('.ve-aviso-mail');

describe('«Descargar errores Excel» en el bloque «A revisar»', () => {
  afterEach(() => {
    Swal.close();
    aviso()?.remove();
  });

  it('Compras: va debajo de «Consultar al cliente» y descarga las del cliente y la operación', async () => {
    const descargar = vi.fn().mockResolvedValue('Errores - Compras - Acme.xlsx');
    const v = await abrir([sinArca('Acme', 'Compras', ['A-0001-00000001'])], descargar);
    const acciones = v.bloque.querySelector('.ve-acciones-revisar')!;
    const botones = [...acciones.querySelectorAll('.ve-boton')];
    expect(botones.map((b) => b.textContent!.trim())).toEqual(['✉ Consultar al cliente', 'Descargar errores Excel']);
    expect(v.excel('Compras').querySelector('svg')).not.toBeNull();

    v.excel('Compras').click();
    await esperar();
    expect(descargar).toHaveBeenCalledWith('Acme', 'Compras');
    expect(aviso()!.textContent).toContain('Errores - Compras - Acme.xlsx');
  });

  it('Ventas y Compras: un botón por operación, con cada mail; en «Todos», los dos', async () => {
    const descargar = vi.fn().mockResolvedValue('Errores - Ventas - Acme.xlsx');
    const v = await abrir([sinArca('Acme', 'Compras', ['A-0001-00000001']), sinArca('Acme', 'Ventas', ['A-0002-00000001'])], descargar);
    const todos = v.bloque.querySelector<HTMLElement>('[data-op-mail=""]')!;
    expect([...todos.querySelectorAll<HTMLButtonElement>('button.ve-boton-excel')].map((b) => b.dataset['op']).sort()).toEqual(['Compras', 'Ventas']);
    const ventas = v.bloque.querySelector<HTMLElement>('[data-op-mail="Ventas"]')!;
    expect(ventas.querySelector('button.ve-boton-excel')!.textContent).toContain('Descargar errores Excel · Ventas');

    ventas.querySelector<HTMLButtonElement>('button.ve-boton-excel')!.click();
    await esperar();
    expect(descargar).toHaveBeenCalledWith('Acme', 'Ventas');
  });

  it('cada cliente pide lo suyo', async () => {
    const descargar = vi.fn().mockResolvedValue('x.xlsx');
    const v = await abrir([sinArca('Acme', 'Compras', ['A-0001-00000001']), sinArca('Beta', 'Compras', ['A-0009-00000001'])], descargar);
    const deBeta = v.popup.querySelector<HTMLButtonElement>('.ve-grupo[data-cliente="Beta"] button.ve-boton-excel')!;
    deBeta.click();
    await esperar();
    expect(descargar).toHaveBeenCalledExactlyOnceWith('Beta', 'Compras');
  });

  it('mientras descarga: «Descargando…», deshabilitado y sin doble clic', async () => {
    let terminar!: (n: string) => void;
    const descargar = vi.fn(() => new Promise<string | null>((r) => (terminar = r)));
    const v = await abrir([sinArca('Acme', 'Compras', ['A-0001-00000001'])], descargar);
    const boton = v.excel('Compras');
    boton.click();
    boton.dispatchEvent(new MouseEvent('click', { bubbles: true }));
    expect(descargar).toHaveBeenCalledTimes(1);
    expect(boton.disabled).toBe(true);
    expect(boton.getAttribute('aria-busy')).toBe('true');
    expect(boton.textContent).toContain('Descargando…');
    terminar('Errores - Compras - Acme.xlsx');
    await esperar();
    expect(boton.disabled).toBe(false);
    expect(boton.textContent).toContain('Descargar errores Excel');
  });

  it('sin partidas: avisa y no hay descarga', async () => {
    const v = await abrir([sinArca('Acme', 'Compras', ['A-0001-00000001'])], vi.fn().mockResolvedValue(null));
    v.excel('Compras').click();
    await esperar();
    expect(aviso()!.textContent).toContain('no hay nada para descargar');
  });

  it('si falla, lo dice y el botón vuelve a andar', async () => {
    const descargar = vi.fn().mockRejectedValue(new Error('No se pudo leer el AUX de Compras'));
    const v = await abrir([sinArca('Acme', 'Compras', ['A-0001-00000001'])], descargar);
    v.excel('Compras').click();
    await esperar();
    expect(aviso()!.textContent).toContain('No se pudo descargar el Excel de errores: No se pudo leer el AUX de Compras');
    expect(v.excel('Compras').disabled).toBe(false);
  });

  it('sin permiso de edición queda deshabilitado (como los mails)', async () => {
    const descargar = vi.fn();
    const v = await abrir([sinArca('Acme', 'Compras', ['A-0001-00000001'])], descargar, false);
    const boton = v.excels()[0];
    expect(boton.disabled).toBe(true);
    expect(boton.hasAttribute('data-errores-excel')).toBe(false);
    boton.click();
    expect(descargar).not.toHaveBeenCalled();
  });

  it('«Consultar al cliente» sigue andando y no descarga el Excel de errores', async () => {
    const descargar = vi.fn();
    const abrirVentana = vi.spyOn(window, 'open').mockReturnValue(null);
    const v = await abrir([sinArca('Acme', 'Compras', ['A-0001-00000001'])], descargar);
    const mail = v.bloque.querySelector<HTMLAnchorElement>('a[data-mail]')!;
    expect(mail.href).toContain('mail.google.com');
    mail.click();
    await new Promise((r) => setTimeout(r, 50));
    expect(descargar).not.toHaveBeenCalled();
    abrirVentana.mockRestore();
  });

  it('sin la función en el contexto (fuera de un control) no aparece', async () => {
    const v = await abrir([sinArca('Acme', 'Compras', ['A-0001-00000001'])], undefined);
    expect(v.excels()).toHaveLength(0);
    expect(v.bloque.querySelector('a[data-mail]')).not.toBeNull();
  });
});
