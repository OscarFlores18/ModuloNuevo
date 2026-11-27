import { adaptar, cuitConGuiones, detectarEmpresa, mismoNombre, sinCae } from './logica';
import { corta } from './verificaciones';

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

  it('primero los que usó la empresa, después todos los productos de Xubio', async () => {
    const { PRODUCTOS_XUBIO, productosSugeridos, recordarProducto } = await import('./logica');
    expect(productosSugeridos('', 'Ventas')).toEqual(PRODUCTOS_XUBIO);
    expect(PRODUCTOS_XUBIO).toContain('Producto al 21% (PROD21)');
    recordarProducto('Logys', 'Publicidad', 'Compras');
    recordarProducto('Logys', 'COMBUSTIBLE', 'Compras');
    const p = productosSugeridos('Logys', 'Compras');
    expect(p.slice(0, 2)).toEqual(['COMBUSTIBLE', 'Publicidad']);
    expect(p.filter((x) => x === 'COMBUSTIBLE')).toHaveLength(1);
    expect(p).toHaveLength(PRODUCTOS_XUBIO.length);
    expect(productosSugeridos('Otra', 'Compras')[0]).toBe(PRODUCTOS_XUBIO[0]);
  });
});

describe('detección tolerante al OCR', () => {
  const empresas = [{ nombre: 'Vendedora Prueba S.A.', cuits: ['30-71080988-3'] }];

  it('encuentra la CUIT con un dígito mal leído', () => {
    const f = detectarEmpresa({ receptor_cuit: '30710809885', emisor_cuit: '20111111112' }, empresas);
    expect(f?.operacion).toBe('Compras');
  });

  it('encuentra la empresa por el nombre si la CUIT no se leyó', () => {
    const f = detectarEmpresa({ emisor: 'VENDEDORA PRUEBA SA', emisor_cuit: '' }, empresas);
    expect([f?.empresa, f?.operacion]).toEqual(['Vendedora Prueba S.A.', 'Ventas']);
  });

  it('no confunde nombres distintos', () => {
    expect(mismoNombre('Vendedora Prueba S.A.', 'Otra Empresa SRL')).toBe(false);
  });
});

describe('avisos cortos con un backend anterior', () => {
  const v = (mensaje: string) => corta({ campo: 'x', nivel: 'aviso', mensaje, sugerencia: null });

  it('arma título y números del mensaje largo', () => {
    const a = v('Los 8 ítems suman 83.974,68 y el neto gravado es 83.974,75 (diferencia 0,07): algún importe está mal leído o falta un ítem.');
    expect([a.titulo, a.datos]).toEqual(['Los ítems no suman el neto', ['Ítems 83.974,68', 'Neto 83.974,75', 'Dif. 0,07']]);
  });

  it('las lecturas distintas quedan para elegir', () => {
    const a = v('Emisor: cada lectura dio algo distinto (FOC FUELS MARKETING $ / FOC FUELS MARKETING SA). Compará con la imagen.');
    expect([a.titulo, a.opciones]).toEqual(['Emisor: ¿cuál es?', ['FOC FUELS MARKETING $', 'FOC FUELS MARKETING SA']]);
  });

  it('lo verificado queda en pocas palabras', () => {
    expect(v('CUIT del emisor: el dígito verificador da bien.').titulo).toBe('CUIT del emisor');
    expect(v('Neto + IVA + otros tributos = total (108.417,98).').titulo).toBe('El total cierra');
    expect(v('El IVA es el 21% del neto gravado.').titulo).toBe('IVA 21%');
  });
});
