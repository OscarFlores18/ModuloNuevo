import { Advertencia } from '../../core/models';
import { armarMail, copiarMail } from './ventana-errores';

const fila = (pares: [string, string | number | null][]) => pares.map(([columna, valor]) => ({ columna, valor }));

describe('Mail «Consultar al cliente»', () => {
  const faltante = {
    tipo: 'faltante_en_xubio',
    cliente: 'PICCININI y ASOCIADOS S.A',
    operacion: 'Compras',
    comprobantes: [
      {
        origen: 'arca',
        fila: fila([['Fecha', '01/10/2025']]),
        mail: fila([
          ['Fecha', '01/10/2025'], ['Tipo', '1 - Factura A'], ['Punto de Venta', 4], ['Número Desde', 400205],
          ['Cód. Autorización', 74517958652179], ['Tipo Doc. Emisor', 'CUIT'], ['Nro. Doc. Emisor', 30707310657],
          ['Denominación Emisor', 'NOVIT S. A.'], ['Imp. Neto Gravado', 62502.25], ['Imp. Op. Exentas', 0],
          ['IVA', 13125.47], ['Imp. Total', 75627.72],
        ]),
      },
    ],
  } as unknown as Advertencia;

  it('usa la estructura del estudio y una tabla con bordes con las columnas de ARCA', () => {
    const m = armarMail(faltante, { emails: { 'PICCININI y ASOCIADOS S.A': 'a@b.com' }, periodo: '10/2025' });
    expect(m.html).toContain('Estimados, buenos días, ¿cómo están?');
    expect(m.html).toContain('falta cargar las percepciones');
    expect(m.html).toContain('centro de costo');
    expect(m.html).toContain('posición del IVA');
    expect(m.html).toContain('border:1px solid #000');
    for (const t of ['Punto de Venta', 'Cód. Autorización', 'Denominación Emisor', 'Imp. Op. Exentas']) expect(m.html).toContain(t);
    expect(m.html).toContain('>74517958652179<');
    expect(m.html).toContain('>62.502,25<');
    expect(m.html).toContain('>-<');
    expect(m.url).toContain('to=a@b.com');
    expect(m.url).not.toContain('body=');
    expect(m.texto).toContain('NOVIT S. A. | 62.502,25 | - | 13.125,47 | 75.627,72');
  });

  it('copia el mail con la tabla en el mismo clic (con texto y HTML)', () => {
    const m = armarMail(faltante, { emails: {}, periodo: '10/2025' });
    const datos: Record<string, string> = {};
    const original = document.execCommand;
    document.execCommand = ((cmd: string) => {
      const ev = new Event('copy') as ClipboardEvent;
      Object.defineProperty(ev, 'clipboardData', { value: { setData: (t: string, v: string) => (datos[t] = v) } });
      document.dispatchEvent(ev);
      return cmd === 'copy';
    }) as typeof document.execCommand;
    try {
      expect(copiarMail(m)).toBe(true);
    } finally {
      document.execCommand = original;
    }
    expect(datos['text/html']).toContain('<table');
    expect(datos['text/plain']).toContain('falta cargar las percepciones');
  });
});
