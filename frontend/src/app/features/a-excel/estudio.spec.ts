import { adaptar, cuitConGuiones, detectarEmpresa, sinCae } from './logica';

const empresas = [
  { nombre: 'Piccinini', cuits: ['30-71080988-3', '20111111112'] },
  { nombre: 'Logys', cuits: ['30709864013'] },
];

describe('Conversor: para el estudio', () => {
  it('la empresa es el receptor: es una compra y la contraparte es el emisor', () => {
    const f = detectarEmpresa({ emisor: 'Bacc App', emisor_cuit: '30716583097', receptor: 'Piccinini', receptor_cuit: '30710809883' }, empresas);
    expect(f).toEqual({ empresa: 'Piccinini', operacion: 'Compras', origen: 'receptor', contraparte: { nombre: 'Bacc App', cuit: '30716583097' } });
  });

  it('la empresa es el emisor: es una venta y la contraparte es el receptor', () => {
    const f = detectarEmpresa({ emisor_cuit: '30-71080988-3', receptor: 'Fiscales', receptor_cuit: '30717538826' }, empresas);
    expect(f?.operacion).toBe('Ventas');
    expect(f?.contraparte).toEqual({ nombre: 'Fiscales', cuit: '30717538826' });
  });

  it('sin CUIT de un cliente no detecta nada', () => {
    expect(detectarEmpresa({ emisor_cuit: '33714877769', receptor_cuit: '30500000000' }, empresas)).toBeNull();
    expect(detectarEmpresa({}, [])).toBeNull();
  });

  it('elegida a mano se adapta igual', () => {
    expect(adaptar({ receptor: 'X', receptor_cuit: '30717538826' }, 'Logys', 'Ventas', 'manual').contraparte.cuit).toBe('30717538826');
  });

  it('el CAE no se muestra en el texto leído', () => {
    expect(sinCae('CAE N°: 86406611601602\nTotal 100')).toBe('CAE N°: (oculto)\nTotal 100');
    expect(cuitConGuiones('30710809883')).toBe('30-71080988-3');
  });
});

describe('Conversor: productos de cada empresa', () => {
  beforeEach(() => localStorage.clear());

  it('primero los que usó la empresa, después los de siempre de la operación', async () => {
    const { productosSugeridos, recordarProducto } = await import('./logica');
    expect(productosSugeridos('', 'Ventas')[0]).toBe('Servicios profesionales');
    recordarProducto('Logys', 'Gastos de limpieza', 'Compras');
    recordarProducto('Logys', 'Refrigerio', 'Compras');
    const p = productosSugeridos('Logys', 'Compras');
    expect(p.slice(0, 2)).toEqual(['Refrigerio', 'Gastos de limpieza']);
    expect(p.filter((x) => x === 'Refrigerio')).toHaveLength(1);
    expect(productosSugeridos('Otra', 'Compras')[0]).toBe('Mercaderías');
  });
});
