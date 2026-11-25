import { ComprobanteExtraido } from '../../core/models';
import { conContraparte, contraparte, cuitValido, recalcularTotales, validarComprobante } from './comprobantes';
import { etiquetaDe } from './convertir';

const base = (cambios: Partial<ComprobanteExtraido> = {}): ComprobanteExtraido => ({
  id: 'f.png#1', archivo: 'f.png', pagina: null, metodo: 'qr', incluir: true,
  operacion: 'Compras', cliente: 'Acme', fecha: '2026-11-03', tipo: 1, punto_venta: 2, numero: 1234,
  cae: '74123456789012',
  emisor: { doc_tipo: 80, doc_nro: '30661792015', nombre: 'MEGATOM S. A.' },
  receptor: { doc_tipo: 80, doc_nro: '20222222223', nombre: 'ACME' },
  moneda: '$', tipo_cambio: 1,
  importes: { 'Imp. Neto Gravado IVA 21%': 1000, 'IVA 21%': 210, 'Imp. Neto Gravado Total': 1000, 'Total IVA': 210, 'Imp. Total': 1210 },
  avisos: [],
  ...cambios,
});

describe('Revisión de comprobantes leídos', () => {
  it('un comprobante completo no tiene avisos', () => {
    expect(validarComprobante(base())).toEqual([]);
  });

  it('avisa lo que falta y lo que no cierra', () => {
    const avisos = validarComprobante(base({ operacion: null, cliente: '', cae: '', importes: { 'Imp. Neto Gravado Total': 1000, 'Imp. Total': 1500 } }));
    expect(avisos).toContain('Falta elegir si es venta o compra.');
    expect(avisos).toContain('Falta elegir el cliente (la empresa en Xubio).');
    expect(avisos.some((a) => a.startsWith('Sin CAE'))).toBe(true);
    expect(avisos.some((a) => a.startsWith('Los importes no suman'))).toBe(true);
  });

  it('valida el CUIT de la contraparte según la operación', () => {
    expect(cuitValido('30-66179201-5')).toBe(true);
    expect(cuitValido('30661792016')).toBe(false);
    const c = base({ emisor: { doc_tipo: 80, doc_nro: '30661792016', nombre: 'X' } });
    expect(validarComprobante(c)).toContain('El CUIT del proveedor (30661792016) no es válido.');
    // En Ventas la contraparte es el receptor: el emisor ya no importa.
    expect(validarComprobante({ ...c, operacion: 'Ventas' })).toEqual([]);
  });

  it('la contraparte se edita en el emisor (compras) o en el receptor (ventas)', () => {
    expect(contraparte(base()).nombre).toBe('MEGATOM S. A.');
    expect(conContraparte(base(), { nombre: 'OTRO' }).emisor.nombre).toBe('OTRO');
    expect(conContraparte(base({ operacion: 'Ventas' }), { nombre: 'OTRO' }).receptor.nombre).toBe('OTRO');
  });

  it('recalcula neto gravado e IVA totales desde las alícuotas', () => {
    const c = recalcularTotales(base({ importes: { 'Imp. Neto Gravado IVA 21%': 1000, 'IVA 21%': 210, 'Imp. Neto Gravado IVA 10,5%': 100, 'IVA 10,5%': 10.5 } }));
    expect(c.importes['Imp. Neto Gravado Total']).toBe(1100);
    expect(c.importes['Total IVA']).toBe(220.5);
  });

  it('etiqueta los archivos elegidos por tipo', () => {
    expect(etiquetaDe('factura.PDF')).toBe('PDF');
    expect(etiquetaDe('foto.jpeg')).toBe('Imagen');
    expect(etiquetaDe('mis comprobantes.csv')).toBe('CSV');
    expect(etiquetaDe('compras octubre.xlsx')).toBe('Compras');
    expect(etiquetaDe('planilla.xls')).toBe('Excel');
  });
});
