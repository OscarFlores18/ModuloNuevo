import { ApplicationConfig, LOCALE_ID, inject, provideAppInitializer, provideBrowserGlobalErrorListeners } from '@angular/core';
import { registerLocaleData } from '@angular/common';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import localeEsAr from '@angular/common/locales/es-AR';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { routes } from './app.routes';
import { EjecucionStore } from './core/services/ejecucion.store';
import { SesionService, sesionInterceptor } from './core/services/sesion.service';

registerLocaleData(localeEsAr);

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withInterceptors([sesionInterceptor])),
    // Antes de la primera pantalla: quién es (pase de ContadorWeb o usuario de prueba)
    // y si hay un control corriendo en el servidor.
    provideAppInitializer(async () => {
      // inject() solo vale antes del primer await.
      const sesion = inject(SesionService);
      const ejecucion = inject(EjecucionStore);
      await sesion.iniciar();
      void ejecucion.revisarActiva();
    }),
    { provide: LOCALE_ID, useValue: 'es-AR' },
  ],
};
