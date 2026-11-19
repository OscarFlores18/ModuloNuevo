import { Component, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink } from '@angular/router';
import { filter, map } from 'rxjs';

interface PasoCircuito {
  numero: number;
  texto: string;
  ruta: string;
  /** Qué se hace en ese paso (al pasar el mouse). */
  ayuda: string;
  /** Las pantallas en las que este paso es el actual. */
  activoEn: string[];
}

/** El trabajo completo, de punta a punta: los mismos 4 botones de arriba, en el orden en que se hace. */
const PASOS: PasoCircuito[] = [
  { numero: 1, texto: 'Clientes', ruta: '/clientes', ayuda: 'Cargá cada cliente con su CUIT y su clave fiscal de ARCA. Se hace una sola vez.', activoEn: ['/clientes'] },
  { numero: 2, texto: 'Nuevo control', ruta: '/control', ayuda: 'Elegí los clientes y el período: los bots bajan ARCA y Xubio y arman la auditoría.', activoEn: ['/control'] },
  { numero: 3, texto: 'Ejecuciones', ruta: '/ejecuciones', ayuda: 'Mirá qué encontró el control y hacé las tareas: corregir en Xubio o consultar al cliente.', activoEn: ['/ejecuciones'] },
  { numero: 4, texto: 'Subir a Xubio', ruta: '/convertir-aux', ayuda: 'Lo que está en ARCA y falta en Xubio se importa desde acá. Después, un control nuevo del mismo período confirma que quedó todo bien.', activoEn: ['/convertir-aux'] },
];

/**
 * Los pasos de todo el trabajo arriba de cada pantalla: dónde estás y qué
 * sigue. Cada paso lleva a su pantalla.
 */
@Component({
  selector: 'app-circuito',
  imports: [RouterLink],
  template: `
    <ol class="cf-circuito" aria-label="Pasos del trabajo" data-tour="circuito">
      <li class="cf-circuito-titulo" aria-hidden="true">Cómo se trabaja</li>
      @for (p of pasos; track p.numero; let ultimo = $last) {
        <li>
          <a [routerLink]="p.ruta" class="cf-circuito-paso" [class.actual]="esActual(p)" [attr.aria-current]="esActual(p) ? 'step' : null" [title]="p.ayuda">
            <span class="cf-circuito-num">{{ p.numero }}</span>{{ p.texto }}
          </a>
          @if (!ultimo) { <span class="cf-circuito-flecha" aria-hidden="true">›</span> }
        </li>
      }
    </ol>
  `,
})
export class Circuito {
  private readonly router = inject(Router);
  protected readonly pasos = PASOS;
  private readonly url = toSignal(
    this.router.events.pipe(
      filter((e): e is NavigationEnd => e instanceof NavigationEnd),
      map((e) => e.urlAfterRedirects),
    ),
    { initialValue: this.router.url },
  );

  protected esActual(p: PasoCircuito): boolean {
    const url = this.url() ?? '';
    return p.activoEn.some((r) => url === r || url.startsWith(r + '/') || url.startsWith(r + '?'));
  }
}
