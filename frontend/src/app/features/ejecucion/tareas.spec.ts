import { describe, expect, it } from 'vitest';

import { sinLoNormal } from './tareas';

describe('sinLoNormal', () => {
  it('saca las aclaraciones de lo que es normal y deja lo que hay que revisar', () => {
    const detalle =
      '1 a revisar: está en Xubio y ARCA no lo informa en este período. 9 son tiques o extractos bancarios: ARCA no los informa en Mis Comprobantes, es normal.';
    expect(sinLoNormal(detalle)).toBe('1 a revisar: está en Xubio y ARCA no lo informa en este período.');
  });

  it('si solo había lo normal queda vacío (la tarea usa el título)', () => {
    expect(sinLoNormal('2 de proveedores del exterior (CUIT genérico): ARCA no los informa, es normal.')).toBe('');
    expect(sinLoNormal(null)).toBe('');
  });
});
