import { Verificacion } from '../../core/models';

/** Si el backend no mandó el título corto (versión anterior), se arma del mensaje. */
export function corta(v: Verificacion): Verificacion {
  if (v.titulo) return v;
  const m = v.mensaje;
  let x: RegExpMatchArray | null;
  if ((x = m.match(/^Los \d+ ítems suman ([\d.,-]+) y el neto gravado es ([\d.,-]+) \(diferencia ([\d.,-]+)\)/)))
    return { ...v, titulo: 'Los ítems no suman el neto', datos: [`Ítems ${x[1]}`, `Neto ${x[2]}`, `Dif. ${x[3]}`] };
  if ((x = m.match(/^Los ítems (\d+) y (\d+) tienen la misma cantidad y precio pero distinto importe \(([\d.,-]+) y ([\d.,-]+)\)/)))
    return { ...v, titulo: `Ítems ${x[1]} y ${x[2]}: mismo precio, otro importe`, datos: [`#${x[1]} ${x[3]}`, `#${x[2]} ${x[4]}`] };
  if ((x = m.match(/^(.+?): cada lectura dio algo distinto \((.+)\)\./)))
    return { ...v, titulo: `${x[1]}: ¿cuál es?`, opciones: x[2].split(' / ') };
  if ((x = m.match(/^(.+?): el dígito verificador da bien/))) return { ...v, titulo: x[1] };
  if (/^Neto \+ IVA .*= total/.test(m)) return { ...v, titulo: 'El total cierra' };
  if ((x = m.match(/^El IVA es el ([\d.,]+)% /))) return { ...v, titulo: `IVA ${x[1]}%` };
  if ((x = m.match(/^Los (\d+) ítems suman el /))) return { ...v, titulo: `Los ${x[1]} ítems suman bien` };
  if ((x = m.match(/^Fecha (\S+?)\.?$/))) return { ...v, titulo: `Fecha ${x[1]}` };
  // Lo demás: hasta el primer «:» o «(», si queda corto.
  const corte = m.split(/[:(]/)[0].replace(/\.$/, '').trim();
  return corte.length >= 6 && corte.length < m.length && corte.length <= 60 ? { ...v, titulo: corte } : v;
}
