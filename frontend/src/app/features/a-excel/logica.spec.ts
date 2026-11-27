import { LecturaExcel } from '../../core/models';
import { aNumero, aTexto, conDesglose, conTributos, ivaDeItems, nivelDe, revisarDesglose, sumaItems, usarSugerencia } from './logica';

const base: LecturaExcel = {
  archivo: 'tique.jpg', tipo: 'comprobante', metodo: 'ocr', encabezado: { emisor_cuit: '33714077769' },
  items: [], totales: { neto_gravado: 63974.75, total: 106417.98 }, iva_alicuotas: [], tributos: [], hojas: [], texto: '', dudas: {}, avisos: [], verificaciones: [],
};

describe('Pasar a Excel', () => {
  it('lee importes escritos de cualquier forma', () => {
    expect(aNumero('83.974,75')).toBe(83974.75);
    expect(aNumero('83974.75')).toBe(83974.75);
    expect(aNumero('$ 1.210')).toBe(1210);
    expect(aNumero('(12,50)')).toBe(-12.5);
    expect(aNumero('')).toBeNull();
    expect(aTexto(83974.75)).toBe('83.974,75');
  });

  it('«Usar» pone la sugerencia en el campo', () => {
    const neto = usarSugerencia(base, { campo: 'neto_gravado', nivel: 'error', mensaje: '', sugerencia: 83974.75 });
    expect(neto.totales['neto_gravado']).toBe(83974.75);
    const cuit = usarSugerencia(base, { campo: 'emisor_cuit', nivel: 'error', mensaje: '', sugerencia: '33714877769' });
    expect(cuit.encabezado['emisor_cuit']).toBe('33714877769');
    expect(base.totales['neto_gravado']).toBe(63974.75); // no cambia el original
  });

  it('el peor estado pinta el campo', () => {
    const v = [
      { campo: 'total', nivel: 'ok' as const, mensaje: '', sugerencia: null },
      { campo: 'total', nivel: 'error' as const, mensaje: '', sugerencia: null },
    ];
    expect(nivelDe(v, 'total')).toBe('error');
    expect(nivelDe(v, 'iva')).toBeNull();
  });

  it('suma el detalle sin errores de coma flotante', () => {
    expect(sumaItems([{ codigo: '', descripcion: '', cantidad: null, precio: null, alicuota: 21, importe: 0.1 },
                      { codigo: '', descripcion: '', cantidad: null, precio: null, alicuota: 21, importe: 0.2 }])).toBe(0.3);
  });

  it('el IVA total es la suma del desglose', () => {
    const l = conDesglose(base, [{ alicuota: 21, base: 1000, importe: 210 }, { alicuota: 10.5, base: 100, importe: 10.5 }]);
    expect(l.totales['iva']).toBe(220.5);
    const sin = conDesglose(l, []);
    expect(sin.totales['iva']).toBeNull();
  });

  it('«Usar» en una fila del desglose cambia solo esa fila', () => {
    const l = conDesglose(base, [{ alicuota: 21, base: 1000, importe: 201 }, { alicuota: 10.5, base: 100, importe: 10.5 }]);
    const r = usarSugerencia(l, { campo: 'iva_0', nivel: 'error', mensaje: '', sugerencia: 210 });
    expect(r.iva_alicuotas.map((f) => f.importe)).toEqual([210, 10.5]);
    expect(r.totales['iva']).toBe(220.5);
  });

  it('compara cada alícuota con el IVA de los ítems', () => {
    const items = [
      { codigo: '', descripcion: 'A', cantidad: 1, precio: 1000, alicuota: 21, importe: 1000 },
      { codigo: '', descripcion: 'B', cantidad: 1, precio: 100, alicuota: 10.5, importe: 100 },
    ];
    // El detalle suma el neto: los importes son sin IVA.
    expect(ivaDeItems(items, { neto_gravado: 1100, total: 1320.5 }).get(21)).toBe(210);
    const l = conDesglose({ ...base, items, totales: { neto_gravado: 1100, total: 1320.5 } }, [
      { alicuota: 21, base: 1000, importe: 210 },
      { alicuota: 10.5, base: 100, importe: 12 },
    ]);
    const filas = revisarDesglose(l);
    expect(filas[0].estado).toBe('ok');
    expect(filas[1].estado).toBe('error'); // el 10,5% de 100 es 10,50
    // Con IVA incluido (tique): 1210 al 21% tiene 210 de IVA.
    expect(ivaDeItems([{ ...items[0], importe: 1210 }], { neto_gravado: 1000, total: 1210 }).get(21)).toBe(210);
  });

  it('cada tributo suma una sola vez, en su total', () => {
    const l = conTributos({ ...base, totales: { ...base.totales, otros_tributos: 6808.52 } }, [
      { codigo: '10', descripcion: 'IMPUESTOS INTERNOS ITEMS', base: 5675.59, importe: 90.54 },
      { codigo: '07', descripcion: 'PERCEPCION IIBB BS AS', base: 63974.76, importe: 6717.98 },
      { codigo: '', descripcion: 'Percepción IIBB CABA', base: null, importe: 10 },
    ]);
    expect(l.totales['impuestos_internos']).toBe(90.54);
    expect(l.totales['percepcion_iibb']).toBe(6717.98);
    expect(l.totales['otros_tributos']).toBe(10); // la de CABA; antes quedaba 6808,52 y se contaba dos veces
  });

  it('borrar un renglón de tributo deja de sumarlo', () => {
    const con = conTributos(base, [
      { codigo: '10', descripcion: 'IMPUESTOS INTERNOS ITEMS', base: null, importe: 90.54 },
      { codigo: '07', descripcion: 'PERCEPCION IIBB BS AS', base: null, importe: 6717.98 },
    ]);
    const sinInternos = conTributos(con, [con.tributos[1]]);
    expect(sinInternos.totales['impuestos_internos']).toBeNull();
    expect(sinInternos.totales['percepcion_iibb']).toBe(6717.98);
    const sinNada = conTributos(sinInternos, []);
    expect(sinNada.totales['percepcion_iibb']).toBeNull();
  });
});
