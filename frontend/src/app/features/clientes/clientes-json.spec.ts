import { leerClientesJson } from './clientes-json';

describe('Clientes pegados como JSON', () => {
  const datos = { cuit_login: '20111111112', clave: 'secreto', cuit_representado: '30222222223' };

  it('formato del config.json, aunque se pegue mal («{CLIENTES{» y sin cerrar)', () => {
    const pegado = '{CLIENTES{"ACME S.A.": {"ARCA": {"cuit_login": "20111111112","clave": "secreto","cuit_representado": "30222222223"}}';
    expect(leerClientesJson(pegado)).toEqual([{ nombre: 'ACME S.A.', arca: datos }]);
  });

  it('JSON válido con varios clientes, CUIT con guiones y mail', () => {
    const r = leerClientesJson(
      JSON.stringify({ CLIENTES: { Uno: { ARCA: { ...datos, cuit_login: '20-11111111-2' }, email: 'a@b.com' }, Dos: { arca: datos } } }),
    );
    expect(r.map((c) => c.nombre)).toEqual(['Uno', 'Dos']);
    expect(r[0].arca.cuit_login).toBe('20111111112');
    expect(r[0].email).toBe('a@b.com');
  });

  it('sin «CLIENTES», un cliente suelto o una lista', () => {
    expect(leerClientesJson(JSON.stringify({ Acme: { ARCA: datos } }))[0].nombre).toBe('Acme');
    expect(leerClientesJson(JSON.stringify({ nombre: 'Acme', arca: datos }))[0].nombre).toBe('Acme');
    expect(leerClientesJson(JSON.stringify([{ nombre: 'A', arca: datos }, { nombre: 'B', arca: datos }]))).toHaveLength(2);
  });

  it('lo que no se puede leer avisa', () => {
    expect(() => leerClientesJson('')).toThrow('Pegá el JSON');
    expect(() => leerClientesJson('hola')).toThrow('no es un JSON válido');
    expect(() => leerClientesJson('{}')).toThrow('ningún cliente');
  });
});
