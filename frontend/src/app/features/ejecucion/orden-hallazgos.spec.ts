import { Advertencia } from '../../core/models';
import { auxDe } from '../../core/services/subida-xubio.service';
import { claveCambio, claveFila, elegirParaAplicar, estaIncluida, excluidasDe, incluir, paraAplicar } from './seleccion';
import { cantidadComprobantes, ordenDe, tareasDe } from './tareas';
import { abreviarTipo, cambiosDisponibles, claveExcel, contarBloques, esPrioridad, ordenarHallazgos, pendientesDe } from './ventana-errores';

function adv(tipo: Advertencia['tipo'], gravedad: Advertencia['gravedad']): Advertencia {
  return { tipo, gravedad, cliente: 'Cliente', operacion: 'Compras', titulo: tipo, detalle: '', acciones: [], total: 1, a_corregir: 1, comprobantes: [], hoja: null };
}

describe('jerarquía de la ventana de hallazgos', () => {
  it('las partidas de ARCA no encontradas van siempre arriba (aunque sean informativas); el resto, de grave a info', () => {
    const lista = [adv('faltante_en_xubio', 'info'), adv('arca_vacio', 'grave'), adv('letra_distinta', 'corregir'), adv('faltante_en_arca', 'corregir'), adv('faltante_en_xubio', 'corregir')];
    const orden = ordenarHallazgos(lista).map((a) => `${a.tipo}/${a.gravedad}`);
    expect(orden).toEqual(['faltante_en_xubio/corregir', 'faltante_en_xubio/info', 'arca_vacio/grave', 'letra_distinta/corregir', 'faltante_en_arca/corregir']);
  });

  it('la prioridad es ARCA → no está en el Libro IVA (aunque sea informativa), no al revés', () => {
    expect(esPrioridad(adv('faltante_en_xubio', 'corregir'))).toBe(true);
    expect(esPrioridad(adv('faltante_en_xubio', 'info'))).toBe(true);
    expect(esPrioridad(adv('faltante_en_arca', 'corregir'))).toBe(false);
  });

  it('las tareas de cargar en Xubio van en «Faltantes en IVA», antes que lo grave', () => {
    const tareas = tareasDe([adv('arca_vacio', 'grave'), adv('letra_distinta', 'corregir'), adv('faltante_en_xubio', 'corregir')]);
    expect(tareas.map((t) => `${t.grupo}/${t.advertencia.tipo}`)).toEqual(['prioridad/faltante_en_xubio', 'primero/arca_vacio', 'xubio/letra_distinta']);
  });

  it('tipo, CUIT e importe distinto cuentan comprobantes (pares ARCA/Xubio), no filas', () => {
    const conFilas = (tipo: Advertencia['tipo'], total: number, a_corregir?: number): Advertencia => ({ ...adv(tipo, 'corregir'), total, a_corregir });
    expect(cantidadComprobantes(conFilas('tipo_distinto', 2, 2))).toBe(1);
    expect(cantidadComprobantes(conFilas('importe_distinto', 6, 3))).toBe(3);
    expect(cantidadComprobantes(conFilas('letra_distinta', 1))).toBe(1);
    expect(ordenDe(conFilas('tipo_distinto', 2, 2))?.orden).toBe('Cambiá el tipo de 1 comprobante en Xubio');
    expect(ordenDe(conFilas('importe_distinto', 2, 1))?.orden).toBe('Corregí el importe de 1 comprobante en Xubio');
    expect(ordenDe(conFilas('tipo_distinto', 4))?.orden).toBe('Cambiá el tipo de 2 comprobantes en Xubio');
  });

  it('los bloques cuentan comprobantes: Error (pares) y lo sin cruce completo, con cuántas ya están explicadas', () => {
    const con = (tipo: Advertencia['tipo'], gravedad: Advertencia['gravedad'], total: number, a_corregir?: number): Advertencia => ({ ...adv(tipo, gravedad), total, a_corregir });
    const n = contarBloques([
      con('letra_distinta', 'corregir', 1),
      con('tipo_distinto', 'corregir', 2, 2),
      con('importe_distinto', 'corregir', 2, 1),
      con('faltante_en_xubio', 'corregir', 6, 2),
      con('faltante_en_arca', 'info', 3, 0),
      con('arca_vacio', 'grave', 0),
    ]);
    // Del lado ARCA nada se da por explicado: lo decide quien sube.
    expect(n).toEqual({ error: 3, soloArca: 6, soloXubio: 3, explicadas: 3 });
  });

  it('con el Excel, «Subir a Xubio» y «A revisar» cuentan sus filas', () => {
    const fila = { fecha: '01/11/2026', tipo: '1 - Factura A', comprobante: 'A-00001-00000001', cuit: '30500000000', razon: 'X', total: 1 };
    const clave = claveExcel('Cliente', 'Compras');
    const sinLibro = new Map([[clave, [fila, fila, fila, fila]]]);
    const sinArca = new Map([[clave, [fila, fila]]]);
    // El Excel tiene las 4 (sin etiquetas: se eligen a mano).
    const falt: Advertencia = { ...adv('faltante_en_xubio', 'corregir'), total: 4, a_corregir: 2 };
    expect(contarBloques([falt], sinLibro, sinArca)).toEqual({ error: 0, soloArca: 4, soloXubio: 2, explicadas: 0 });
    // Sin el Excel, todas las del hallazgo.
    expect(contarBloques([falt])).toEqual({ error: 0, soloArca: 4, soloXubio: 0, explicadas: 0 });
  });

  it('el KPI de la operación suma Error + Subir a Xubio + A revisar, sin lo ya explicado', () => {
    const con = (tipo: Advertencia['tipo'], gravedad: Advertencia['gravedad'], total: number, a_corregir?: number): Advertencia => ({ ...adv(tipo, gravedad), total, a_corregir });
    const lista = [con('letra_distinta', 'corregir', 1), con('faltante_en_xubio', 'corregir', 6, 2), con('faltante_en_arca', 'corregir', 5, 3)];
    expect(pendientesDe(lista, 'Cliente', 'Compras')).toBe(10);
    expect(pendientesDe(lista, 'Cliente', 'Ventas')).toBe(0);
  });

  it('la selección de qué se sube: todo tildado de entrada, se destilda por fila y queda por control', () => {
    const fila = claveFila('30-50000000-0', 'A-00001-00000010');
    expect(fila).toBe('30500000000|A-00001-00000010');
    expect(estaIncluida('ej-prueba', 'Cliente', 'Compras', fila)).toBe(true);
    incluir('ej-prueba', 'Cliente', 'Compras', fila, false);
    expect(estaIncluida('ej-prueba', 'Cliente', 'Compras', fila)).toBe(false);
    expect(excluidasDe('ej-prueba', 'Cliente', 'Compras')).toEqual([fila]);
    expect(excluidasDe('ej-prueba', 'Cliente', 'Ventas')).toEqual([]);
    expect(estaIncluida('otro-control', 'Cliente', 'Compras', fila)).toBe(true);
    incluir('ej-prueba', 'Cliente', 'Compras', fila, true);
    expect(excluidasDe('ej-prueba', 'Cliente', 'Compras')).toEqual([]);
    // «Aplicar cambios»: de entrada no hay nada elegido; tildar solo elige.
    const par = claveCambio('Cliente', 'Compras', 'importe', fila);
    expect(paraAplicar('ej-prueba', par)).toBe(false);
    elegirParaAplicar('ej-prueba', [par], true);
    expect(paraAplicar('ej-prueba', par)).toBe(true);
    elegirParaAplicar('ej-prueba', [par], false);
    expect(paraAplicar('ej-prueba', par)).toBe(false);
  });

  it('arma el cambio de cada par de «Error» con la fila de Xubio y el dato de ARCA', () => {
    const celdas = (o: Record<string, string | number>) => Object.entries(o).map(([columna, valor]) => ({ columna, valor }));
    const importe: Advertencia = {
      ...adv('importe_distinto', 'corregir'),
      total: 2,
      a_corregir: 1,
      comprobantes: [
        { origen: 'arca', fila: celdas({ Fecha: '01/04/2026', Tipo: '1 - Factura A', Comprobante: 'A-00002-00528012', 'Nro. Doc.': '30714687650', 'Denominación': 'SOLUCIONES', 'Imp. Total': 116450.4 }) },
        { origen: 'xubio', fila: celdas({ Fecha: '01/04/2026', Tipo: 'Factura', Comprobante: 'A-00002-00528012', CUIT: '30-71468765-0', Proveedor: 'SOLUCIONES', Total: 117149.1 }) },
      ],
    } as Advertencia;
    const [c] = cambiosDisponibles([importe]);
    expect(c.datos).toEqual({ cliente: 'Cliente', operacion: 'Compras', campo: 'importe', cuit: '30714687650', pv: 2, numero: 528012, comprobante: 'A-00002-00528012', valorArca: 116450.4 });
    expect(c.clave).toBe('Cliente|Compras|importe|30714687650|A-00002-00528012');
    // Lo grave o los faltantes no se corrigen con «Aplicar cambios».
    expect(cambiosDisponibles([adv('faltante_en_xubio', 'corregir')])).toEqual([]);
  });

  it('abrevia el tipo de comprobante', () => {
    expect(abreviarTipo('1 - Factura A')).toBe('FC A');
    expect(abreviarTipo('3 - Nota de Crédito A')).toBe('NC A');
    expect(abreviarTipo('63 - Liquidación A')).toBe('Liq. A');
    expect(abreviarTipo('63 - Liquidaciónes A')).toBe('Liq. A');
    expect(abreviarTipo('Nota Crédito')).toBe('NC');
    expect(abreviarTipo('Extracto Bancario')).toBe('Extracto');
    expect(abreviarTipo('Recibo X')).toBe('Rec. X');
    expect(abreviarTipo('Otro tipo')).toBe('Otro tipo');
  });

  it('encuentra el AUX de la operación entre los archivos del control', () => {
    const archivos = [
      { nombre: 'Mis Comprobantes Recibidos.xlsx', tamano: 1, modificado: '', tipo: 'arca' as const },
      { nombre: 'AUX - Control de facturas Compras con AFIP - 10-26 - PICCININI - 1er control.xlsx', tamano: 1, modificado: '', tipo: 'auditoria' as const },
    ];
    expect(auxDe({ archivos }, 'Compras')).toBe(archivos[1].nombre);
    expect(auxDe({ archivos }, 'Ventas')).toBeNull();
  });
});
