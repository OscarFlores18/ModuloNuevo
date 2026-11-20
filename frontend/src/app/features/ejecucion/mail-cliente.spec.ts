import { Advertencia } from '../../core/models';
import { abrirMail, armarMail, copiarMail } from './ventana-errores';

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

  it('usa la plantilla oficial y una tabla con bordes con las 12 columnas de ARCA', () => {
    const m = armarMail(faltante, { emails: { 'PICCININI y ASOCIADOS S.A': 'a@b.com' }, periodo: '10/2025' });
    expect(m.texto).toBe(
      [
        'Estimados buenos días, ¿cómo están?',
        '',
        'Enviamos detalles de facturas que están en Arca y que fueron cargadas a Xubio; falta cargar las percepciones. Por favor enviarlas en cuanto las tengan.',
        '',
        'También aclarar en la factura a qué centro de costo imputamos y, en el caso que fueron abonadas, aclarar de qué forma fueron canceladas.',
        '',
        'Fecha | Tipo | Punto de Venta | Número Desde | Cód. Autorización | Tipo Doc. Emisor | Nro. Doc. Emisor | Denominación Emisor | Imp. Neto Gravado | Imp. Op. Exentas | IVA | Imp. Total',
        '01/10/2025 | 1 - Factura A | 4 | 400205 | 74517958652179 | CUIT | 30707310657 | NOVIT S. A. | 62.502,25 | - | 13.125,47 | 75.627,72',
        '',
        'Quedamos a la espera, muchas gracias.',
        '',
        'Saludos cordiales y buen comienzo de semana!!',
      ].join('\n'),
    );
    expect(m.html).toContain('border-collapse:collapse;border:1px solid #64748b');
    expect(m.html).toContain('<th style="border:1px solid #94a3b8;padding:5px 8px;background-color:#e2e8f0;font-weight:bold');
    expect(m.html).toContain('text-align:center;white-space:nowrap">74517958652179<');
    expect(m.html).toContain('text-align:right;white-space:nowrap">62.502,25<');
    expect(m.html).toContain('text-align:right;white-space:nowrap;font-weight:bold">75.627,72<');
    expect(m.url).toContain('to=a%40b.com');
    // El cuerpo no va en la URL (perdería la tabla): se pega con formato.
    expect(m.url).not.toContain('body=');
    // Respaldo sin portapapeles: el texto plano, con los saltos de línea codificados.
    expect(new URL(m.urlConTexto!).searchParams.get('body')).toBe(m.texto);
    expect(m.urlConTexto).toContain('%0A');
    expect(m.urlConTexto).not.toContain('+');
  });

  it('Factura C: los importes vacíos o nulos salen con «-»', () => {
    const c = {
      ...faltante,
      comprobantes: [{ origen: 'arca', fila: [], mail: fila([['Tipo', '11 - Factura C'], ['Imp. Neto Gravado', null], ['IVA', ''], ['Imp. Total', 105000]]) }],
    } as unknown as Advertencia;
    expect(armarMail(c, { emails: {}, periodo: '10/2025' }).texto).toContain('11 - Factura C | - | - | 105.000,00');
  });

  it('abre Gmail con la cuenta configurada (authuser), el destinatario y el asunto', () => {
    const m = armarMail(faltante, { emails: { 'PICCININI y ASOCIADOS S.A': 'a@b.com' }, periodo: '10/2025', cuenta: 'tito@estudio.com.ar' });
    expect(m.url.startsWith('https://mail.google.com/mail/?view=cm&fs=1&authuser=tito%40estudio.com.ar&to=a%40b.com&su=')).toBe(true);
    const q = new URL(m.url).searchParams;
    expect(q.get('authuser')).toBe('tito@estudio.com.ar');
    expect(q.get('su')).toBe('Consulta de comprobantes de compras · 10/2025 · PICCININI y ASOCIADOS S.A');
    // Un usuario que no es un mail no se usa: Gmail abre con la cuenta de siempre.
    expect(armarMail(faltante, { emails: {}, periodo: '10/2025', cuenta: 'usuario' }).url).not.toContain('authuser');
  });

  it('largo (más de 1800 caracteres): sin respaldo de texto en la URL', () => {
    const muchas = {
      ...faltante,
      comprobantes: Array.from({ length: 30 }, () => (faltante.comprobantes as unknown[])[0]),
    } as unknown as Advertencia;
    const m = armarMail(muchas, { emails: {}, periodo: '10/2025', cuenta: 'tito@estudio.com.ar' });
    expect(m.texto.length).toBeGreaterThan(1800);
    expect(m.urlConTexto).toBeNull();
    expect(m.url).toContain('authuser=');
  });

  it('copia con ClipboardItem (HTML y texto) y recién después abre Gmail sin cuerpo, con el aviso', async () => {
    const m = armarMail(faltante, { emails: {}, periodo: '10/2025' });
    const pasos: string[] = [];
    const g = globalThis as { ClipboardItem?: unknown };
    const tenia = g.ClipboardItem;
    g.ClipboardItem = class {
      constructor(readonly tipos: Record<string, Blob>) {}
    };
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { write: async (items: { tipos: Record<string, Blob> }[]) => void pasos.push(`copia:${Object.keys(items[0].tipos).join(',')}`) },
    });
    const abrir = vi.spyOn(window, 'open').mockImplementation((u) => (pasos.push(`abre:${String(u)}`), null));
    try {
      await abrirMail(m);
      expect(pasos).toEqual(['copia:text/html,text/plain', `abre:${m.url}`]);
      expect(document.querySelector('.ve-aviso-mail')?.textContent).toBe('Correo con tabla copiado al portapapeles. Presiona Ctrl + V en Gmail');
    } finally {
      delete (navigator as { clipboard?: unknown }).clipboard;
      abrir.mockRestore();
      g.ClipboardItem = tenia;
      document.querySelector('.ve-aviso-mail')?.remove();
    }
  });

  it('con el backend en esta PC y destinatario: pide el Ctrl+V automático antes de abrir Gmail', async () => {
    const conMail = armarMail(faltante, { emails: { 'PICCININI y ASOCIADOS S.A': 'a@b.com' }, periodo: '10/2025' });
    const sinMail = armarMail(faltante, { emails: {}, periodo: '10/2025' });
    const pasos: string[] = [];
    const g = globalThis as { ClipboardItem?: unknown };
    const tenia = g.ClipboardItem;
    g.ClipboardItem = class {};
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { write: async () => void pasos.push('copia') } });
    const abrir = vi.spyOn(window, 'open').mockImplementation(() => (pasos.push('abre'), null));
    const pegar = async () => void pasos.push('pegar');
    try {
      await abrirMail(conMail, pegar);
      expect(pasos).toEqual(['copia', 'pegar', 'abre']);
      expect(document.querySelector('.ve-aviso-mail')?.textContent).toContain('se pega solo');
      // Sin destinatario el cursor queda en «Para»: no se pega solo.
      pasos.length = 0;
      await abrirMail(sinMail, pegar);
      expect(pasos).toEqual(['copia', 'abre']);
      expect(document.querySelector('.ve-aviso-mail')?.textContent).toContain('Ctrl + V');
    } finally {
      delete (navigator as { clipboard?: unknown }).clipboard;
      abrir.mockRestore();
      g.ClipboardItem = tenia;
      document.querySelector('.ve-aviso-mail')?.remove();
    }
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
