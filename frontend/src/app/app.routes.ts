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
  {
    path: 'convertir-aux',
    title: 'Faltantes a Xubio · Control de Facturas',
    loadComponent: () => import('./features/convertir/convertir').then((m) => m.ConvertirPantalla),
    // Si quedó algo cargado en Xubio sin apretar «Importar», se recuerda antes de salir.
    canDeactivate: [(pantalla: { puedeSalir?: () => Promise<boolean> }) => pantalla.puedeSalir?.() ?? true],
  },
  { path: '**', redirectTo: '' },
];
