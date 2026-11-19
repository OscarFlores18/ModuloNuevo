import { HttpClient, HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { catchError, firstValueFrom, throwError } from 'rxjs';
import { environment } from '../../../environments/environment';

/**
 * Contraseña liviana de la sección Clientes (ver backend/app/core/acceso_clientes.py).
 *
 * No es un login de la app: «Nuevo control» funciona sin ella. Se pide una sola vez
 * en cada computadora; la primera vez, lo que se escriba queda como contraseña. El
 * token queda en localStorage (sobrevive a cerrar la app) y va en `X-Clientes-Token`.
 * Solo se vuelve a pedir con «Bloquear» o si cambia la contraseña.
 */
const CLAVE_TOKEN = 'control-fct.clientes';

interface Token {
  token: string;
  expira: number;
}

function leerToken(): Token | null {
  try {
    const t = JSON.parse(globalThis.localStorage?.getItem(CLAVE_TOKEN) ?? 'null') as Token | null;
    return t?.token && t.expira * 1000 > Date.now() ? t : null;
  } catch {
    return null;
  }
}

function guardarToken(t: Token | null): void {
  try {
    if (t) globalThis.localStorage?.setItem(CLAVE_TOKEN, JSON.stringify(t));
    else globalThis.localStorage?.removeItem(CLAVE_TOKEN);
  } catch {
    // Sin almacenamiento (modo privado): dura hasta recargar.
  }
}

@Injectable({ providedIn: 'root' })
export class AccesoClientesService {
  private readonly http = inject(HttpClient);
  private readonly url = `${environment.apiUrl}/clientes/acceso`;

  private readonly tokenActual = signal<Token | null>(leerToken());
  /** null mientras no se consultó al backend. */
  readonly configurada = signal<boolean | null>(null);
  readonly desbloqueado = signal(false);

  token(): string | null {
    const t = this.tokenActual();
    return t && t.expira * 1000 > Date.now() ? t.token : null;
  }

  async revisar(): Promise<void> {
    try {
      const r = await firstValueFrom(this.http.get<{ configurada: boolean; desbloqueado: boolean }>(this.url));
      this.configurada.set(r.configurada);
      // Sin contraseña todavía: se pide crearla antes de mostrar los clientes.
      const abierto = r.configurada && r.desbloqueado;
      this.desbloqueado.set(abierto);
      if (!abierto) this.olvidar();
    } catch {
      // Sin backend: se muestra el candado; los pedidos avisan el error.
      this.configurada.set(true);
      this.desbloqueado.set(false);
    }
  }

  /** Entra (o crea la contraseña la primera vez). Tira el error con el mensaje de la API. */
  async entrar(clave: string): Promise<{ creada: boolean }> {
    const r = await firstValueFrom(this.http.post<Token & { creada: boolean }>(this.url, { clave }));
    const t = { token: r.token, expira: r.expira };
    this.tokenActual.set(t);
    guardarToken(t);
    this.configurada.set(true);
    this.desbloqueado.set(true);
    return { creada: r.creada };
  }

  async bloquear(): Promise<void> {
    try {
      await firstValueFrom(this.http.delete<void>(this.url));
    } catch {
      // Igual se olvida acá.
    }
    this.olvidar();
    this.desbloqueado.set(false);
  }

  /** El backend contestó «bloqueado» (token vencido o servidor reiniciado). */
  marcarBloqueado(): void {
    this.olvidar();
    this.configurada.set(true);
    this.desbloqueado.set(false);
  }

  private olvidar(): void {
    this.tokenActual.set(null);
    guardarToken(null);
  }
}

/** Le agrega el token a los pedidos de clientes y re-bloquea si el backend lo rechaza. */
export const accesoClientesInterceptor: HttpInterceptorFn = (req, next) => {
  if (!req.url.startsWith(`${environment.apiUrl}/clientes`)) return next(req);
  const acceso = inject(AccesoClientesService);
  const token = acceso.token();
  const pedido = token ? req.clone({ setHeaders: { 'X-Clientes-Token': token } }) : req;
  return next(pedido).pipe(
    catchError((e: unknown) => {
      if (e instanceof HttpErrorResponse && e.status === 401 && e.error?.detail?.code === 'clientes_bloqueado') {
        acceso.marcarBloqueado();
      }
      return throwError(() => e);
    }),
  );
};
