import { Component, inject, signal } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Usuario } from './core/models';
import { ClientesStore } from './core/services/clientes.store';
import { EjecucionStore } from './core/services/ejecucion.store';
import { SesionService, USUARIOS_DE_PRUEBA } from './core/services/sesion.service';

const CLAVE_MENU = 'control-fct.menu_plegado';

function leerPlegada(): boolean {
  try {
    return typeof localStorage !== 'undefined' && localStorage.getItem(CLAVE_MENU) === '1';
  } catch {
    return false;
  }
}

@Component({
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  selector: 'app-root',
  styleUrl: './app.css',
  templateUrl: './app.html',
})
export class App {
  protected readonly sesion = inject(SesionService);
  protected readonly ejecucion = inject(EjecucionStore);
  protected readonly clientes = inject(ClientesStore);
  protected readonly usuariosDePrueba = USUARIOS_DE_PRUEBA;

  /** Menú plegado (solo íconos). Se recuerda en este navegador. */
  protected readonly plegada = signal(leerPlegada());

  constructor() {
    // Para el aviso de clientes con datos incompletos en el menú.
    void this.clientes.cargar();
  }

  protected alternarMenu(): void {
    this.plegada.update((p) => !p);
    try {
      localStorage.setItem(CLAVE_MENU, this.plegada() ? '1' : '0');
    } catch {
      // Sin almacenamiento (modo privado): queda plegada solo hasta recargar.
    }
  }

  protected rolesDe(u: Usuario): string {
    const roles = u.roles.map((r) => (r === 'supervisor' ? 'Supervisor' : 'Usuario')).join(' · ') || 'Sin rol';
    return u.nivel_nombre ? `${u.nivel_nombre} · ${roles}` : roles;
  }

  protected iniciales(nombre: string): string {
    const partes = nombre.match(/\p{L}+/gu) ?? [];
    return ((partes[0]?.[0] ?? '?') + (partes[1]?.[0] ?? '')).toUpperCase();
  }

  protected cambiarUsuario(id: string): void {
    this.sesion.cambiar(id);
  }
}
