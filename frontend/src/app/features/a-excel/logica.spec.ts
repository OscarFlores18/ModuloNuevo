import { LecturaExcel } from '../../core/models';
import { aNumero, aTexto, nivelDe, sumaItems, usarSugerencia } from './logica';

const base: LecturaExcel = {
  archivo: 'tique.jpg', tipo: 'comprobante', metodo: 'ocr', encabezado: { emisor_cuit: '33714077769' },
  items: [], totales: { neto_gravado: 63974.75, total: 106417.98 }, hojas: [], texto: '', dudas: {}, avisos: [], verificaciones: [],
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
});
