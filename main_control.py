"""
CONTROL DE FACTURAS (compras y ventas) - version independiente de DRLiqui.

Archivos necesarios en la misma carpeta:
    main_control.py, bot_arca.py, bot_xubio.py, bot_xubio_common.py, auditor_fo.py

Uso:  python main_control.py
Requiere: Google Chrome instalado y las librerias customtkinter, playwright,
openpyxl, pandas y numpy.

Comparte el formato de config.json con DRLiqui (los datos de liquidacion que ya
tenga un cliente se conservan al editarlo desde aca).
"""
import os
import sys
import json
import re
import tkinter as tk
from tkinter import filedialog, messagebox
from datetime import datetime
import threading
import customtkinter as ctk

import bot_arca
import bot_xubio
import auditor_fo

# --- CONFIGURACIÓN VISUAL ---
# Paleta oscura renovada: mismos tonos violetas de siempre (VIOLET_MAIN /
# TITLE_COLOR), pero con fondos más cálidos/neutros y tarjetas con borde
# sutil en vez del borde violeta grueso original, buscando una interfaz más
# compacta y prolija (ver preview_estilo.html, aprobado antes de este
# cambio). Sin emojis en ningún texto de la interfaz ni de los logs, por
# regla del proyecto.
ctk.set_appearance_mode("Dark")
BG_COLOR = "#0e0d0f"
FRAME_BG = "#171519"
FIELD_BG = "#0e0d0f"
BORDER_COLOR = "#2e2933"
VIOLET_MAIN = "#7828C8"
VIOLET_HOVER = "#5c1b99"
VIOLET_SOFT = "#241a33"
TEXT_COLOR = "#e4e1e6"
TEXT_MUTED = "#8f8a96"
TITLE_COLOR = "#D194FF"
GREEN_BTN = "#4f9d63"
GREEN_HOVER = "#3f7f4f"
RED_BTN = "#c25b52"
RED_HOVER = "#a34840"

# Subcarpeta de salida: <Carpeta>/<Cliente>/<mmaaaa>/Control de Facturas
SUBCARPETA_CONTROL = "Control de Facturas"

FONT_LBL = ("Segoe UI", 12)
FONT_ENTRY = ("Segoe UI", 13)
FONT_TITULO = ("Segoe UI", 13, "bold")
FONT_SUB = ("Segoe UI", 11)

# --- CLASE PARA REDIRIGIR PRINTS AL TXT ---
class RedirectStdout:
    def __init__(self, logger_func):
        self.logger_func = logger_func
        self.terminal = sys.__stdout__

    def write(self, text):
        # En el .exe armado con --windowed (sin consola) no existe stdout real:
        # sys.__stdout__ es None. Sin este chequeo, el primer print() de
        # cualquier bot (hay muchos) tira AttributeError y corta la app.
        if self.terminal:
            try:
                self.terminal.write(text)
            except Exception:
                pass
        if text.strip():
            self.logger_func(text.strip())

    def flush(self):
        if self.terminal:
            try:
                self.terminal.flush()
            except Exception:
                pass


class CentralAutomatizacionApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Control de Facturas")
        self.root.geometry("900x600")
        self.root.minsize(900, 600)
        self.root.configure(fg_color=BG_COLOR)

        self.ruta_descarga = tk.StringVar(value=os.path.join(os.path.expanduser("~"), "Downloads"))
        self.clientes_seleccionados = []
        self.cache_widgets_disp = {}
        self.preguntar_siempre = True

        self.config = self.cargar_configuracion()
        self.credenciales_xubio = self.cargar_credenciales_xubio()
        self.crear_interfaz()
        self.construir_cache_disponibles()

        # Redirigir consola al txt
        sys.stdout = RedirectStdout(self.log)

    def obtener_base_dir(self):
        # Frozen-safe: bajo PyInstaller, __file__ apunta a la carpeta interna
        # del bundle (no a la carpeta real del .exe). sys.executable sí apunta
        # ahí, así que los .json quedan al lado del .exe como corresponde.
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    def cargar_configuracion(self):
        base_dir = self.obtener_base_dir()
        self.ruta_json = os.path.join(base_dir, "config.json")
        try:
            with open(self.ruta_json, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

        if "clientes" not in data:
            data["clientes"] = {}

        # Antes las credenciales de Xubio del usuario (usuario/clave/"recordar")
        # vivían mezcladas acá adentro, bajo la clave "xubio_global", junto con
        # las claves de AGIP/ARBA/ARCA de TODOS los clientes. Se migran una
        # sola vez a un archivo separado (xubio_credenciales.json) y se sacan
        # de config.json para no volver a mezclar ambas cosas.
        if "xubio_global" in data:
            ruta_xubio = os.path.join(base_dir, "xubio_credenciales.json")
            if not os.path.exists(ruta_xubio):
                try:
                    with open(ruta_xubio, "w", encoding="utf-8") as f:
                        json.dump(data["xubio_global"], f, indent=4, ensure_ascii=False)
                except Exception:
                    pass
            del data["xubio_global"]
            # Persistir la limpieza ya mismo: si no, "xubio_global" queda
            # colgado en el archivo en disco hasta el próximo guardado.
            try:
                with open(self.ruta_json, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
            except Exception:
                pass

        return data

    def cargar_credenciales_xubio(self):
        self.ruta_json_xubio = os.path.join(self.obtener_base_dir(), "xubio_credenciales.json")
        try:
            with open(self.ruta_json_xubio, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "user": data.get("user", ""),
                "pass": data.get("pass", ""),
                "recordar": data.get("recordar", False),
            }
        except Exception:
            return {"user": "", "pass": "", "recordar": False}

    def guardar_configuracion(self):
        # Los clientes se guardan siempre ordenados alfabéticamente (sin distinguir mayúsculas)
        if isinstance(self.config.get("clientes"), dict):
            self.config["clientes"] = dict(sorted(self.config["clientes"].items(), key=lambda kv: kv[0].casefold()))
        try:
            with open(self.ruta_json, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar config.json: {e}")

    def log(self, texto):
        """ Log exclusivo para archivo TXT """
        timestamp = datetime.now().strftime("%H:%M:%S")
        linea = f"[{timestamp}] {texto}\n"
        
        ruta = self.ruta_descarga.get()
        if os.path.exists(ruta):
            try:
                with open(os.path.join(ruta, "debug_log.txt"), "a", encoding="utf-8") as f:
                    f.write(linea)
            except: pass

    def toggle_password(self):
        if self.entry_xubio_pass.cget("show") == "*":
            self.entry_xubio_pass.configure(show="")
            self.btn_show_pass.configure(text="OCULTAR")
        else:
            self.entry_xubio_pass.configure(show="*")
            self.btn_show_pass.configure(text="VER")

    def guardar_xubio_credenciales(self):
        # Se guarda en xubio_credenciales.json, un archivo aparte de
        # config.json: config.json guarda las claves de AGIP/ARBA/ARCA/Xubio
        # de todos los CLIENTES, y no debe mezclarse con la credencial propia
        # del usuario que abre la app.
        recordar = self.var_recordar_xubio.get()
        self.credenciales_xubio["recordar"] = recordar

        if recordar:
            self.credenciales_xubio["user"] = self.entry_xubio_user.get().strip()
            self.credenciales_xubio["pass"] = self.entry_xubio_pass.get().strip()
        else:
            self.credenciales_xubio["user"] = ""
            self.credenciales_xubio["pass"] = ""

        try:
            with open(self.ruta_json_xubio, "w", encoding="utf-8") as f:
                json.dump(self.credenciales_xubio, f, indent=4, ensure_ascii=False)
        except Exception as e:
            messagebox.showerror("Error", f"No se pudo guardar xubio_credenciales.json: {e}")

    def pedir_confirmacion_pausa(self, empresa_actual):
        if not self.preguntar_siempre:
            return "continuar"
            
        resultado = ["continuar"]
        evento = threading.Event()
        timeout_secs = 15
        
        def build_ui():
            top = ctk.CTkToplevel(self.root)
            top.title("Control de Procesos")
            top.geometry("450x170")
            top.configure(fg_color=BG_COLOR)
            top.attributes("-topmost", True)
            top.grab_set()

            lbl = ctk.CTkLabel(top, text=f"Se completó la empresa:\n{empresa_actual}\n\nContinuando automáticamente en {timeout_secs}s...", font=ctk.CTkFont(size=14, weight="bold"))
            lbl.pack(pady=15)

            def resolver(accion):
                resultado[0] = accion
                top.grab_release()
                top.destroy()
                evento.set()

            f_btn = ctk.CTkFrame(top, fg_color="transparent")
            f_btn.pack(pady=5)
            ctk.CTkButton(f_btn, text="Continuar", width=100, command=lambda: resolver("continuar"), fg_color=GREEN_BTN, hover_color=GREEN_HOVER).pack(side="left", padx=10)
            ctk.CTkButton(f_btn, text="Cancelar Todo", width=100, command=lambda: resolver("cancelar"), fg_color=RED_BTN, hover_color=RED_HOVER).pack(side="left", padx=10)
            ctk.CTkButton(f_btn, text="No preguntar más", width=120, command=lambda: resolver("no_preguntar"), fg_color=VIOLET_MAIN, hover_color=VIOLET_HOVER).pack(side="left", padx=10)

            def countdown(left):
                if not top.winfo_exists(): return
                if left <= 0:
                    resolver("continuar")
                else:
                    lbl.configure(text=f"Se completó la empresa:\n{empresa_actual}\n\nContinuando automáticamente en {left}s...")
                    self.root.after(1000, countdown, left - 1)

            countdown(timeout_secs)
            top.protocol("WM_DELETE_WINDOW", lambda: resolver("continuar"))
            
        self.root.after(0, build_ui)
        evento.wait()
        return resultado[0]

    def crear_interfaz(self):
        main_frame = ctk.CTkFrame(self.root, fg_color=BG_COLOR)
        main_frame.pack(fill="both", expand=True, padx=12, pady=12)

        main_frame.grid_columnconfigure(0, weight=0)
        main_frame.grid_columnconfigure(1, weight=1)
        main_frame.grid_rowconfigure(0, weight=1)

        # ==========================================
        # IZQUIERDA: dos paneles apilados, Disponibles arriba (más grande)
        # y A Procesar abajo (más chico) — con la ventana más grande
        # (900x600) vuelven a entrar los dos paneles clásicos en vez de
        # la lista única con checkbox de la versión compacta anterior.
        # ==========================================
        frame_clientes = ctk.CTkFrame(main_frame, fg_color="transparent", width=290)
        frame_clientes.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        frame_clientes.grid_propagate(False)
        frame_clientes.grid_columnconfigure(0, weight=1)
        frame_clientes.grid_rowconfigure(0, weight=3)
        frame_clientes.grid_rowconfigure(1, weight=2)

        # ---- Panel superior: Disponibles ----
        # Se arma con grid (no pack): así el buscador y el botón "+ Nuevo
        # Cliente" siempre reservan su fila de tamaño fijo, y solo la lista
        # (fila con weight=1) cede espacio cuando el contenido no entra —
        # con pack + expand=True, la lista podía absorber TODO el espacio
        # disponible y dejar el botón sin lugar (invisible), el mismo tipo
        # de bug ya visto antes en la pestaña "Papel de Liquidación".
        f_disp = ctk.CTkFrame(frame_clientes, fg_color=FRAME_BG, border_color=BORDER_COLOR, border_width=1)
        f_disp.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        f_disp.grid_columnconfigure(0, weight=1)
        f_disp.grid_rowconfigure(2, weight=1)

        f_disp_top = ctk.CTkFrame(f_disp, fg_color="transparent")
        f_disp_top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        ctk.CTkLabel(f_disp_top, text="DISPONIBLES", font=FONT_TITULO, text_color=TITLE_COLOR).pack(side="left")
        self.lbl_contador_disp = ctk.CTkLabel(f_disp_top, text="(0)", font=FONT_LBL, text_color=TEXT_MUTED)
        self.lbl_contador_disp.pack(side="left", padx=6)

        self.search_var = tk.StringVar()
        self.search_var.trace("w", lambda *args: self.refrescar_vista_clientes())
        ctk.CTkEntry(f_disp, textvariable=self.search_var, placeholder_text="Buscar...", height=30, font=FONT_ENTRY, fg_color=FIELD_BG, border_color=BORDER_COLOR).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 6))

        self.scroll_clientes = ctk.CTkScrollableFrame(f_disp, fg_color="transparent")
        self.scroll_clientes.grid(row=2, column=0, sticky="nsew", padx=6, pady=(0, 6))

        ctk.CTkButton(f_disp, text="+ Nuevo Cliente", height=30, font=FONT_LBL, command=lambda: self.abrir_ventana_cliente(), fg_color=VIOLET_MAIN, hover_color=VIOLET_HOVER).grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 10))

        # ---- Panel inferior: A Procesar (misma lógica de grid) ----
        f_proc = ctk.CTkFrame(frame_clientes, fg_color=FRAME_BG, border_color=BORDER_COLOR, border_width=1)
        f_proc.grid(row=1, column=0, sticky="nsew")
        f_proc.grid_columnconfigure(0, weight=1)
        f_proc.grid_rowconfigure(1, weight=1)

        f_proc_top = ctk.CTkFrame(f_proc, fg_color="transparent")
        f_proc_top.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 4))
        ctk.CTkLabel(f_proc_top, text="A PROCESAR", font=FONT_TITULO, text_color=TITLE_COLOR).pack(side="left")
        self.lbl_contador_proc = ctk.CTkLabel(f_proc_top, text="(0)", font=FONT_LBL, text_color=TEXT_MUTED)
        self.lbl_contador_proc.pack(side="left", padx=6)

        self.scroll_seleccionados = ctk.CTkScrollableFrame(f_proc, fg_color="transparent")
        self.scroll_seleccionados.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))

        ctk.CTkButton(f_proc, text="Limpiar Selección", height=30, font=FONT_LBL, command=self.limpiar_seleccionados, fg_color=VIOLET_SOFT, hover_color=RED_HOVER, text_color=TEXT_COLOR).grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 10))

        # ==========================================
        # DERECHA: Parámetros comunes + Tabs
        # ==========================================
        frame_der = ctk.CTkFrame(main_frame, fg_color="transparent")
        frame_der.grid(row=0, column=1, sticky="nsew")
        frame_der.grid_rowconfigure(1, weight=1)
        frame_der.grid_columnconfigure(0, weight=1)

        # ---- Parámetros comunes ----
        f_params = ctk.CTkFrame(frame_der, fg_color=FRAME_BG, border_color=BORDER_COLOR, border_width=1)
        f_params.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        f_params_grid = ctk.CTkFrame(f_params, fg_color="transparent")
        f_params_grid.pack(fill="x", padx=12, pady=10)
        f_params_grid.grid_columnconfigure(1, weight=1)
        f_params_grid.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(f_params_grid, text="Desde:", font=FONT_LBL).grid(row=0, column=0, sticky="w")
        self.entry_desde = ctk.CTkEntry(f_params_grid, width=100, height=30, placeholder_text="DD/MM/AAAA", font=FONT_ENTRY, fg_color=FIELD_BG, border_color=BORDER_COLOR)
        self.entry_desde.grid(row=0, column=1, sticky="w", padx=(6, 16))

        ctk.CTkLabel(f_params_grid, text="Hasta:", font=FONT_LBL).grid(row=0, column=2, sticky="w")
        self.entry_hasta = ctk.CTkEntry(f_params_grid, width=100, height=30, placeholder_text="DD/MM/AAAA", font=FONT_ENTRY, fg_color=FIELD_BG, border_color=BORDER_COLOR)
        self.entry_hasta.grid(row=0, column=3, sticky="w", padx=6)

        ctk.CTkLabel(f_params_grid, text="Carpeta:", font=FONT_LBL).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.entry_ruta = ctk.CTkEntry(f_params_grid, textvariable=self.ruta_descarga, height=30, state="readonly", font=FONT_ENTRY, fg_color=FIELD_BG, border_color=BORDER_COLOR)
        self.entry_ruta.grid(row=1, column=1, columnspan=2, sticky="ew", padx=6, pady=(8, 0))
        ctk.CTkButton(f_params_grid, text="...", width=36, height=30, font=FONT_LBL, command=self.seleccionar_carpeta, fg_color=VIOLET_MAIN, hover_color=VIOLET_HOVER).grid(row=1, column=3, sticky="w", padx=6, pady=(8, 0))

        ctk.CTkLabel(f_params_grid, text="Usuario Xubio:", font=FONT_LBL).grid(row=2, column=0, sticky="w", pady=(8, 0))
        self.entry_xubio_user = ctk.CTkEntry(f_params_grid, height=30, font=FONT_ENTRY, fg_color=FIELD_BG, border_color=BORDER_COLOR)
        self.entry_xubio_user.grid(row=2, column=1, sticky="ew", padx=(6, 16), pady=(8, 0))
        self.entry_xubio_user.insert(0, self.credenciales_xubio.get("user", ""))

        ctk.CTkLabel(f_params_grid, text="Contraseña:", font=FONT_LBL).grid(row=2, column=2, sticky="w", pady=(8, 0))
        f_pass = ctk.CTkFrame(f_params_grid, fg_color="transparent")
        f_pass.grid(row=2, column=3, sticky="ew", padx=6, pady=(8, 0))
        self.entry_xubio_pass = ctk.CTkEntry(f_pass, height=30, show="*", font=FONT_ENTRY, fg_color=FIELD_BG, border_color=BORDER_COLOR)
        self.entry_xubio_pass.pack(side="left", fill="x", expand=True)
        self.entry_xubio_pass.insert(0, self.credenciales_xubio.get("pass", ""))
        self.btn_show_pass = ctk.CTkButton(f_pass, text="Ver", width=42, height=30, font=FONT_LBL, fg_color=VIOLET_SOFT, hover_color=VIOLET_HOVER, text_color=TITLE_COLOR, command=self.toggle_password)
        self.btn_show_pass.pack(side="left", padx=(6, 0))

        self.var_recordar_xubio = ctk.BooleanVar(value=self.credenciales_xubio.get("recordar", False))
        ctk.CTkCheckBox(f_params_grid, text="Recordar credenciales", variable=self.var_recordar_xubio, fg_color=VIOLET_MAIN, hover_color=VIOLET_HOVER, font=FONT_LBL).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # ---- Panel de Control de Facturas (sin pestañas) ----
        f_control = ctk.CTkFrame(frame_der, fg_color=FRAME_BG, border_color=BORDER_COLOR, border_width=1)
        f_control.grid(row=1, column=0, sticky="nsew")
        ctk.CTkLabel(f_control, text="CONTROL DE FACTURAS", font=FONT_TITULO, text_color=TITLE_COLOR).pack(anchor="w", padx=18, pady=(12, 0))
        self.construir_tab_scrappy(f_control)


    def construir_tab_scrappy(self, parent):
        f_op = ctk.CTkFrame(parent, fg_color=FRAME_BG)
        f_op.pack(fill="x", pady=(10, 6))
        self.var_op_ventas = ctk.IntVar(value=1)
        self.var_op_compras = ctk.IntVar(value=0)
        ctk.CTkCheckBox(f_op, text="Procesar VENTAS", variable=self.var_op_ventas, fg_color=VIOLET_MAIN, hover_color=VIOLET_HOVER, font=FONT_LBL, checkbox_width=18, checkbox_height=18).pack(side="left", padx=18)
        ctk.CTkCheckBox(f_op, text="Procesar COMPRAS", variable=self.var_op_compras, fg_color=VIOLET_MAIN, hover_color=VIOLET_HOVER, font=FONT_LBL, checkbox_width=18, checkbox_height=18).pack(side="left", padx=18)

        f_pasos = ctk.CTkFrame(parent, fg_color=FRAME_BG)
        f_pasos.pack(fill="x", pady=8)
        self.var_paso_arca = ctk.IntVar(value=1)
        self.var_paso_xubio = ctk.IntVar(value=1)
        self.var_paso_cruce = ctk.IntVar(value=1)
        ctk.CTkCheckBox(f_pasos, text="1. ARCA", variable=self.var_paso_arca, fg_color=VIOLET_MAIN, hover_color=VIOLET_HOVER, font=FONT_LBL, checkbox_width=18, checkbox_height=18).pack(side="left", padx=18)
        ctk.CTkCheckBox(f_pasos, text="2. Xubio", variable=self.var_paso_xubio, fg_color=VIOLET_MAIN, hover_color=VIOLET_HOVER, font=FONT_LBL, checkbox_width=18, checkbox_height=18).pack(side="left", padx=12)
        ctk.CTkCheckBox(f_pasos, text="3. Auditoría", variable=self.var_paso_cruce, fg_color=VIOLET_MAIN, hover_color=VIOLET_HOVER, font=FONT_LBL, checkbox_width=18, checkbox_height=18).pack(side="left", padx=12)

        ctk.CTkButton(parent, text="EJECUTAR CONTROL DE FACTURAS", command=self.ejecutar_hilo_scrappy, fg_color=GREEN_BTN, hover_color=GREEN_HOVER, height=38, font=ctk.CTkFont(family="Segoe UI", weight="bold", size=14)).pack(side="bottom", fill="x", padx=12, pady=12)

    def seleccionar_carpeta(self):
        c = filedialog.askdirectory(initialdir=self.ruta_descarga.get(), title="Seleccionar carpeta")
        if c: self.ruta_descarga.set(c)

    def truncar_nombre(self, texto, largo=18):
        # Los nombres de clientes son de largo variable y la fila del panel
        # tiene ancho fijo (nombre + botones Editar/+). Un CTkLabel no
        # trunca solo: si el texto es más largo que el espacio disponible,
        # su ancho "natural" empuja a los botones fuera de la fila (se ven
        # recortados/superpuestos). Se trunca acá con puntos suspensivos;
        # los botones siguen operando sobre el nombre completo (closures
        # usan "texto", no el truncado). Además los botones se empacan
        # ANTES que la etiqueta, para que siempre reserven su espacio
        # primero sin importar qué tan largo sea el nombre.
        return texto if len(texto) <= largo else texto[:largo - 1] + "…"

    def construir_cache_disponibles(self):
        # Dos paneles: DISPONIBLES (todos los clientes que no están en la
        # lista "A Procesar") y A PROCESAR (los ya elegidos). Se arma el
        # cache de filas de Disponibles una sola vez; A Procesar se
        # reconstruye entera cada vez que cambia (suele ser una lista corta).
        for widget in self.scroll_clientes.winfo_children(): widget.destroy()
        self.cache_widgets_disp.clear()

        for nombre in sorted(self.config.get("clientes", {}).keys(), key=str.casefold):
            item = ctk.CTkFrame(self.scroll_clientes, fg_color="transparent")

            btn_agregar = ctk.CTkButton(item, text="+", width=26, height=24, font=FONT_LBL, fg_color=VIOLET_MAIN, hover_color=VIOLET_HOVER, command=lambda c=nombre: self.agregar_cliente(c))
            btn_agregar.pack(side="right", padx=(4, 0))

            btn_edit = ctk.CTkButton(item, text="Editar", width=52, height=24, font=("Segoe UI", 10), fg_color=VIOLET_SOFT, hover_color=VIOLET_HOVER, text_color=TITLE_COLOR, command=lambda c=nombre: self.abrir_ventana_cliente(c))
            btn_edit.pack(side="right")

            ctk.CTkLabel(item, text=self.truncar_nombre(nombre), font=FONT_LBL, anchor="w").pack(side="left", padx=(4, 4), fill="x", expand=True)

            self.cache_widgets_disp[nombre] = {"frame": item}
        self.refrescar_vista_clientes()
        self.refrescar_vista_seleccionados()

    def refrescar_vista_clientes(self):
        filtro = self.search_var.get().lower()
        self.scroll_clientes._parent_canvas.yview_moveto(0)

        visibles = 0
        for w in self.cache_widgets_disp.values(): w["frame"].pack_forget()
        for nombre, w in self.cache_widgets_disp.items():
            if nombre not in self.clientes_seleccionados and filtro in nombre.lower():
                w["frame"].pack(fill="x", pady=1)
                visibles += 1
        self.lbl_contador_disp.configure(text=f"({visibles})")


    def refrescar_vista_seleccionados(self):
        for widget in self.scroll_seleccionados.winfo_children(): widget.destroy()

        for nombre in self.clientes_seleccionados:
            item = ctk.CTkFrame(self.scroll_seleccionados, fg_color=VIOLET_SOFT)
            item.pack(fill="x", pady=1)
            btn_quitar = ctk.CTkButton(item, text="x", width=26, height=24, font=FONT_LBL, fg_color=RED_BTN, hover_color=RED_HOVER, command=lambda c=nombre: self.quitar_cliente(c))
            btn_quitar.pack(side="right", padx=(4, 4), pady=3)
            ctk.CTkLabel(item, text=self.truncar_nombre(nombre), font=FONT_LBL, text_color=TITLE_COLOR, anchor="w").pack(side="left", padx=(6, 4), pady=3, fill="x", expand=True)

        self.lbl_contador_proc.configure(text=f"({len(self.clientes_seleccionados)})")

    def agregar_cliente(self, cliente):
        if cliente not in self.clientes_seleccionados:
            self.clientes_seleccionados.append(cliente)
        self.refrescar_vista_clientes()
        self.refrescar_vista_seleccionados()

    def quitar_cliente(self, cliente):
        if cliente in self.clientes_seleccionados:
            self.clientes_seleccionados.remove(cliente)
        self.refrescar_vista_clientes()
        self.refrescar_vista_seleccionados()

    def limpiar_seleccionados(self):
        self.clientes_seleccionados.clear()
        self.refrescar_vista_clientes()
        self.refrescar_vista_seleccionados()

    def abrir_ventana_cliente(self, cliente_edit=None):
        pop = ctk.CTkToplevel(self.root)
        pop.title("Editar Cliente" if cliente_edit else "Nuevo Cliente")
        pop.geometry("480x470")
        pop.configure(fg_color=BG_COLOR)
        pop.grab_set()

        scroll = ctk.CTkScrollableFrame(pop, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=10, pady=5)

        data = self.config.get("clientes", {}).get(cliente_edit, {}) if cliente_edit else {}

        ctk.CTkLabel(scroll, text="Nombre del Cliente:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", pady=(2, 0), padx=5)
        ent_nombre = ctk.CTkEntry(scroll, width=420)
        ent_nombre.pack(pady=(0, 5), padx=5)
        if cliente_edit:
            ent_nombre.insert(0, cliente_edit)

        def crear_fila(parent, texto, valor):
            row = ctk.CTkFrame(parent, fg_color="transparent")
            row.pack(fill="x", pady=2, padx=10)
            ctk.CTkLabel(row, text=texto, width=130, anchor="w").pack(side="left")
            entry = ctk.CTkEntry(row, width=260)
            entry.pack(side="right")
            entry.insert(0, valor)
            return entry

        f_arca = ctk.CTkFrame(scroll, fg_color=FRAME_BG); f_arca.pack(fill="x", pady=4)
        ctk.CTkLabel(f_arca, text="ARCA (AFIP)", text_color=TITLE_COLOR, font=ctk.CTkFont(weight="bold")).pack(pady=(5, 0))
        e_arca_cuitL = crear_fila(f_arca, "CUIT Login:", data.get("arca", {}).get("cuit_login", ""))
        e_arca_pass = crear_fila(f_arca, "Clave:", data.get("arca", {}).get("clave", ""))
        e_arca_cuitR = crear_fila(f_arca, "CUIT Repres.:", data.get("arca", {}).get("cuit_representado", ""))

        f_xubio = ctk.CTkFrame(scroll, fg_color=FRAME_BG); f_xubio.pack(fill="x", pady=4)
        ctk.CTkLabel(f_xubio, text="XUBIO", text_color=TITLE_COLOR, font=ctk.CTkFont(weight="bold")).pack(pady=(5, 0))
        e_xubio_url = crear_fila(f_xubio, "URL (href):", data.get("xubio", {}).get("url", ""))

        def guardar():
            nombre_nuevo = ent_nombre.get().strip()
            if not nombre_nuevo: return
            if "clientes" not in self.config: self.config["clientes"] = {}

            if cliente_edit and nombre_nuevo != cliente_edit:
                if cliente_edit in self.config["clientes"]:
                    del self.config["clientes"][cliente_edit]
                if cliente_edit in self.clientes_seleccionados:
                    self.clientes_seleccionados.remove(cliente_edit)
                    self.clientes_seleccionados.append(nombre_nuevo)

            nuevo = dict(data)  # conserva las claves que no se editan aca (AGIP, ARBA, categoria, jurisdicciones)
            nuevo.update({
                "arca": {"cuit_login": e_arca_cuitL.get(), "clave": e_arca_pass.get(), "cuit_representado": e_arca_cuitR.get()},
                "xubio": {"url": e_xubio_url.get()},
            })
            self.config["clientes"][nombre_nuevo] = nuevo

            self.guardar_configuracion()
            self.construir_cache_disponibles()
            pop.destroy()

        def eliminar():
            if messagebox.askyesno("Eliminar", f"¿Seguro que deseas eliminar a {cliente_edit}?"):
                del self.config["clientes"][cliente_edit]
                self.guardar_configuracion()
                if cliente_edit in self.clientes_seleccionados:
                    self.clientes_seleccionados.remove(cliente_edit)
                self.construir_cache_disponibles()
                pop.destroy()

        frame_btns = ctk.CTkFrame(scroll, fg_color="transparent")
        frame_btns.pack(fill="x", pady=(15, 10), padx=5)
        ctk.CTkButton(frame_btns, text="GUARDAR", command=guardar, fg_color=GREEN_BTN, hover_color=GREEN_HOVER).pack(side="right", padx=5)
        if cliente_edit:
            ctk.CTkButton(frame_btns, text="BORRAR", command=eliminar, fg_color=RED_BTN, hover_color=RED_HOVER).pack(side="left", padx=5)

    def validar_basicos(self):
        if not self.clientes_seleccionados:
            messagebox.showwarning("Aviso", "Seleccioná al menos un cliente.")
            return False
        if not self.entry_desde.get() or not self.entry_hasta.get():
            messagebox.showwarning("Aviso", "Completá las fechas.")
            return False
        try:
            _, mes, anio = self.entry_desde.get().split("/")
        except ValueError:
            messagebox.showwarning("Aviso", "La fecha 'Desde' debe tener el formato DD/MM/AAAA")
            return False
        return True

    def validar_credenciales_xubio(self, usa_xubio_scrappy=False):
        """Exige usuario y contraseña de Xubio si el flujo incluye la descarga de Xubio."""
        if not usa_xubio_scrappy:
            return True
        us_xubio = self.entry_xubio_user.get().strip()
        pw_xubio = self.entry_xubio_pass.get().strip()
        if not us_xubio or not pw_xubio:
            messagebox.showwarning(
                "Aviso",
                "Tildaste el paso de Xubio, pero falta el usuario y/o la contraseña de Xubio.\n\n"
                "Completá 'Usuario Xubio' y 'Contraseña' antes de iniciar, o destildá el paso de Xubio."
            )
            return False
        return True

    def construir_carpeta_cliente(self, cli, mes, anio):
        """<Carpeta>/<Cliente>/<mmaaaa>/Control de Facturas"""
        empresa_limpia = re.sub(r'[\\/*?:"<>|]', "", cli).strip()
        carpeta_final = os.path.join(self.ruta_descarga.get(), empresa_limpia, f"{mes}{anio}", SUBCARPETA_CONTROL)
        os.makedirs(carpeta_final, exist_ok=True)
        return carpeta_final

    def ejecutar_hilo_scrappy(self):
        if not self.validar_basicos(): return
        if not self.validar_credenciales_xubio(usa_xubio_scrappy=(self.var_paso_xubio.get() == 1)): return
        self.guardar_xubio_credenciales()
        self.preguntar_siempre = True
        
        messagebox.showinfo("Iniciando", "El control de facturas comenzará en segundo plano.")
        threading.Thread(target=self._run_scrappy, daemon=True).start()

    def _run_scrappy(self):
        fecha_desde = self.entry_desde.get()
        fecha_hasta = self.entry_hasta.get()
        _, mes, anio = fecha_desde.split("/")
        
        hacer_ventas = self.var_op_ventas.get() == 1
        hacer_compras = self.var_op_compras.get() == 1
        us_xubio = self.entry_xubio_user.get()
        pw_xubio = self.entry_xubio_pass.get()

        self.log(f"[INICIO] CONTROL DE FACTURAS")
        self.log("=========================================\n")

        for index, cli in enumerate(self.clientes_seleccionados, 1):
            self.log(f"\n[{index}/{len(self.clientes_seleccionados)}] PROCESANDO: {cli}")
            datos = self.config.get("clientes", {}).get(cli, {})
            
            cuit_login = datos.get("arca", {}).get("cuit_login", "")
            clave_arca = datos.get("arca", {}).get("clave", "")
            cuit_repres = datos.get("arca", {}).get("cuit_representado", "")
            url_xubio = datos.get("xubio", {}).get("url", "")
            
            carpeta_final = self.construir_carpeta_cliente(cli, mes, anio)
            self.log(f" -> Directorio Destino: {SUBCARPETA_CONTROL}")

            if self.var_paso_arca.get() == 1:
                if hacer_ventas:
                    try: bot_arca.ejecutar_extraccion(cuit_login, clave_arca, cuit_repres, fecha_desde, fecha_hasta, "Emitidos", cli, carpeta_final, bajar_excel=True, bajar_csv=False)
                    except Exception as e: self.log(f" [ERROR] ARCA (Ventas): {str(e)}")
                if hacer_compras:
                    try: bot_arca.ejecutar_extraccion(cuit_login, clave_arca, cuit_repres, fecha_desde, fecha_hasta, "Recibidos", cli, carpeta_final, bajar_excel=True, bajar_csv=False)
                    except Exception as e: self.log(f" [ERROR] ARCA (Compras): {str(e)}")

            if self.var_paso_xubio.get() == 1:
                try: bot_xubio.ejecutar_extraccion(us_xubio, pw_xubio, cli, url_xubio, fecha_desde, fecha_hasta, hacer_ventas, hacer_compras, carpeta_final)
                except Exception as e: self.log(f" [ERROR] XUBIO: {str(e)}")

            if self.var_paso_cruce.get() == 1:
                if hacer_ventas:
                    try: auditor_fo.procesar_cruce(cli, "Ventas", fecha_desde, fecha_hasta, carpeta_final)
                    except Exception as e: self.log(f" [ERROR] CRUCE (Ventas): {str(e)}")
                if hacer_compras:
                    try: auditor_fo.procesar_cruce(cli, "Compras", fecha_desde, fecha_hasta, carpeta_final)
                    except Exception as e: self.log(f" [ERROR] CRUCE (Compras): {str(e)}")
            
            self.log("-----------------------------------------")
            
            if index < len(self.clientes_seleccionados):
                accion = self.pedir_confirmacion_pausa(cli)
                if accion == "cancelar":
                    self.log("\n[CANCELADO] PROCESO CANCELADO.")
                    return
                elif accion == "no_preguntar":
                    self.preguntar_siempre = False

        self.log("\n[OK] PROCESO DE CONTROL DE FACTURAS FINALIZADO.")
        def show_finish(): messagebox.showinfo("Terminado", "El control de facturas finalizó correctamente.")
        self.root.after(0, show_finish)


if __name__ == "__main__":
    app = ctk.CTk()
    gui = CentralAutomatizacionApp(app)
    app.mainloop()
