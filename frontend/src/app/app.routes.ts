import { Routes } from '@angular/router';
import { inicioSegunPerspectiva, soloAccesoTotal } from './core/services/perspectiva.service';

/**
 * Sin pantalla de ingreso: quién es lo da ContadorWeb (ver core/services/sesion.service.ts).
 * Empleado no tiene Nuevo control; el resto lo ve con las acciones bloqueadas.
 */
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: inicioSegunPerspectiva },
  {
    path: 'control',
    canActivate: [soloAccesoTotal],
    title: 'Nuevo control · Control de Facturas',
    loadComponent: () => import('./features/control/control').then((m) => m.NuevoControl),
  },
  {
    path: 'ejecuciones',
    title: 'Ejecuciones · Control de Facturas',
    loadComponent: () => import('./features/ejecucion/ejecucion').then((m) => m.EjecucionPantalla),
  },
  {
    path: 'ejecuciones/:id',
    title: 'Ejecución · Control de Facturas',
    loadComponent: () => import('./features/ejecucion/ejecucion').then((m) => m.EjecucionPantalla),
  },
  {
    path: 'clientes',
    title: 'Clientes · Control de Facturas',
    loadComponent: () => import('./features/clientes/clientes').then((m) => m.ClientesPantalla),
  },
  {
    path: 'convertir-aux',
    title: 'Subir a Xubio · Control de Facturas',
    loadComponent: () => import('./features/convertir/convertir').then((m) => m.ConvertirPantalla),
  },
  // Directo a la pantalla de inicio: un redirect a '' no vuelve a evaluar la ruta '' y quedaba en blanco.
  { path: '**', redirectTo: inicioSegunPerspectiva },
];
