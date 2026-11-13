import { Routes } from '@angular/router';

/** Sin pantalla de ingreso: quién es lo da ContadorWeb (ver core/services/sesion.service.ts). */
export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'control' },
  {
    path: 'control',
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
  { path: '**', redirectTo: '' },
];
