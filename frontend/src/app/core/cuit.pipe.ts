import { Pipe, PipeTransform } from '@angular/core';

/** '20235102162' -> '20-23510216-2'. Lo que no tiene 11 dígitos queda como vino. */
export function formatearCuit(cuit: string | null | undefined): string {
  const digitos = (cuit ?? '').replace(/\D/g, '');
  return digitos.length === 11 ? `${digitos.slice(0, 2)}-${digitos.slice(2, 10)}-${digitos.slice(10)}` : (cuit ?? '');
}

/** Once dígitos y el dígito verificador correcto (módulo 11). Admite guiones. */
export function cuitValida(cuit: string | null | undefined): boolean {
  const d = (cuit ?? '').replace(/\D/g, '');
  if (d.length !== 11) return false;
  const pesos = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2];
  const suma = pesos.reduce((s, p, i) => s + p * Number(d[i]), 0);
  const resto = 11 - (suma % 11);
  const verificador = resto === 11 ? 0 : resto === 10 ? 9 : resto;
  return verificador === Number(d[10]);
}

/** La CUIT con los guiones de siempre: se lee de un vistazo con qué contribuyente se opera. */
@Pipe({ name: 'cuit' })
export class CuitPipe implements PipeTransform {
  transform(cuit: string | null | undefined): string {
    return formatearCuit(cuit);
  }
}
