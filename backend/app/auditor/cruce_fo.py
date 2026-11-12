"""Auditoría FO 04-01: cruce entre comprobantes de ARCA y el Libro IVA de Xubio.

Reglas:
- No escribe en consola: reporta con `log(mensaje)`.
- Devuelve la ruta del Excel generado.
- Si falta alguno de los dos insumos, falla con `ArchivoNoEncontrado`.
"""

import os
import re
from typing import Callable

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.utils.dataframe import dataframe_to_rows

from ..core.errors import ArchivoNoEncontrado

def formatear_mes_anio(fecha_str):
    partes = fecha_str.split('/')
    if len(partes) == 3:
        return f"{partes[1]}-{partes[2][-2:]}"
    return fecha_str.replace('/', '-')

def procesar_cruce(
    empresa,
    operacion,
    fecha_desde,
    fecha_hasta,
    directorio_guardado,
    ruta_arca_manual=None,
    ruta_xubio_manual=None,
    log: Callable[[str], None] = print,
) -> str:
    """Genera el control de facturas y devuelve la ruta del Excel."""
    log(f"      [INFO] Iniciando cruce FO 04-01 para {empresa} ({operacion})")
    
    # 1. RUTAS DE LOS ARCHIVOS
    tipo_arca = "Emitidos" if operacion == "Ventas" else "Recibidos"
    mes_anio_desde = formatear_mes_anio(fecha_desde)
    mes_anio_hasta = formatear_mes_anio(fecha_hasta)
    f_desde_limpia = fecha_desde.replace("/", "-")
    f_hasta_limpia = fecha_hasta.replace("/", "-")
    
    cliente_limpio_arca = re.sub(r'[\\/*?:"<>|]', "", empresa)
    nombre_seguro_xubio = "".join(c for c in empresa if c.isalnum() or c in "._- ").strip()
    
    ruta_arca = ruta_arca_manual if ruta_arca_manual else os.path.join(directorio_guardado, f"Mis comprobantes - {tipo_arca} - {cliente_limpio_arca} - {mes_anio_desde} al {mes_anio_hasta}.xlsx")
    ruta_xubio = ruta_xubio_manual if ruta_xubio_manual else os.path.join(directorio_guardado, f"IVA {operacion.upper()} - {nombre_seguro_xubio} - {f_desde_limpia} al {f_hasta_limpia}.xlsx")
    
    ruta_salida = os.path.join(directorio_guardado, f"AUX - Control de facturas {operacion} con AFIP - {mes_anio_desde} - {cliente_limpio_arca}.xlsx")

    if not os.path.exists(ruta_arca):
        raise ArchivoNoEncontrado(
            f"No se encontró el archivo de ARCA esperado:\n{ruta_arca}",
            detalle={"cliente": empresa, "paso": "auditoria", "ruta": ruta_arca},
        )
    if not os.path.exists(ruta_xubio):
        raise ArchivoNoEncontrado(
            f"No se encontró el archivo de Xubio esperado:\n{ruta_xubio}",
            detalle={"cliente": empresa, "paso": "auditoria", "ruta": ruta_xubio},
        )

    # =========================================================
    # LIMPIEZA DE NÚMEROS Y FECHAS
    # =========================================================
    def format_to_dd_mm_yyyy(x):
        if pd.isna(x): return ''
        s = str(x).strip()
        if s == '' or s.lower() == 'nan' or s.lower() == 'nat' or 'total' in s.lower(): return ''
        s = s.split(' ')[0] 
        m1 = re.match(r'^(\d{4})[-/](\d{2})[-/](\d{2})$', s)
        if m1: return f"{m1.group(3)}/{m1.group(2)}/{m1.group(1)}"
        m2 = re.match(r'^(\d{2})[-/](\d{2})[-/](\d{4})$', s)
        if m2: return f"{m2.group(1)}/{m2.group(2)}/{m2.group(3)}"
        if s.isdigit() and int(s) > 30000:
            try: return pd.to_datetime(int(s), unit='D', origin='1899-12-30').strftime('%d/%m/%Y')
            except: pass
        return s

    def parse_num(val):
        """ Limpiador universal de números: soporta floats, strings de Excel o de CSV. """
        if pd.isna(val) or val == '' or str(val).strip() == '-': return 0.0
        if isinstance(val, (int, float)): return float(val)
        v = str(val).replace('$', '').replace(' ', '').strip()
        if v == '': return 0.0
        is_neg = v.startswith('-') or (v.startswith('(') and v.endswith(')'))
        v = re.sub(r'[^\d,\.]', '', v)
        if ',' in v and '.' in v:
            v = v.replace('.', '').replace(',', '.')
        elif ',' in v:
            v = v.replace(',', '.')
        try:
            num = float(v)
            return -num if is_neg else num
        except:
            return 0.0

    # 1. CARGA Y LIMPIEZA DE ARCA
    df_arca = pd.read_csv(ruta_arca, skiprows=1) if ruta_arca.endswith('.csv') else pd.read_excel(ruta_arca, skiprows=1)
    df_arca.columns = df_arca.columns.str.strip()
    
    if 'Tipo' in df_arca.columns: df_arca.dropna(subset=['Tipo'], inplace=True)
    else: df_arca.dropna(how='all', inplace=True)

    df_arca.insert(0, 'Clave_Conciliacion', '')
    def extraer_letra(t):
        texto = str(t).upper()
        
        # 1. Buscamos inteligentemente las letras estándar (A, B, C, E, M) que estén solas como "palabra"
        match = re.search(r'\b([ABCEM])\b', texto)
        if match:
            return match.group(1)
            
        # 2. Si es un comprobante muy raro que no tiene letra aislada, usamos el método viejo (al revés)
        match_fallback = re.search(r'([A-Z])', texto[::-1])
        return match_fallback.group(1) if match_fallback else ''
    
    df_arca_temp = df_arca.copy()
    letra_tmp = df_arca_temp['Tipo'].apply(extraer_letra) if 'Tipo' in df_arca_temp.columns else pd.Series(['']*len(df_arca_temp))
    pto_tmp = df_arca_temp['Punto de Venta'].fillna(0).astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(5) if 'Punto de Venta' in df_arca_temp.columns else pd.Series(['00000']*len(df_arca_temp))
    num_tmp = df_arca_temp['Número Desde'].fillna(0).astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(8) if 'Número Desde' in df_arca_temp.columns else pd.Series(['00000000']*len(df_arca_temp))
    
    df_arca_temp['Clave_Py'] = letra_tmp + "-" + pto_tmp + "-" + num_tmp
    df_arca['Clave_Conciliacion'] = df_arca_temp['Clave_Py']
    df_arca_temp = df_arca_temp[df_arca_temp['Clave_Py'].astype(str).str.strip() != '-00000-00000000']
    
    arca_map = {
        'fecha': next((c for c in df_arca.columns if 'fecha' in str(c).lower()), None),
        'tipo': next((c for c in df_arca.columns if 'tipo' in str(c).lower() and 'cambio' not in str(c).lower()), None),
        'pto': next((c for c in df_arca.columns if 'punto de venta' in str(c).lower()), None),
        'num': next((c for c in df_arca.columns if 'número desde' in str(c).lower()), None),
        'cuit': df_arca.columns[8] if len(df_arca.columns) > 8 else None, 
        'prov': next((c for c in df_arca.columns if 'denominaci' in str(c).lower()), None),
        'neto': next((c for c in df_arca.columns if 'neto gravado total' in str(c).lower() or ('neto' in str(c).lower() and 'total' in str(c).lower())), 
                     next((c for c in df_arca.columns if 'neto' in str(c).lower() and 'gravado' in str(c).lower() and '0%' not in str(c).lower() and 'no' not in str(c).lower()), None)),
        'exe': next((c for c in df_arca.columns if 'exent' in str(c).lower() or 'no gravado' in str(c).lower()), None),
        'iva': next((c for c in df_arca.columns if 'total iva' in str(c).lower() or 'iva' == str(c).lower().strip()), None),
        'trib': next((c for c in df_arca.columns if 'otros tributos' in str(c).lower() or 'tributos' in str(c).lower()), None),
        'tot': next((c for c in df_arca.columns if 'imp. total' in str(c).lower() or ('total' in str(c).lower() and 'iva' not in str(c).lower())), None),
        'tc': next((c for c in df_arca.columns if 'tipo cambio' in str(c).lower()), None)
    }

    if arca_map['fecha']:
        df_arca[arca_map['fecha']] = df_arca[arca_map['fecha']].apply(format_to_dd_mm_yyyy)
        df_arca_temp[arca_map['fecha']] = df_arca[arca_map['fecha']]

    dups_a = set(df_arca_temp['Clave_Py'][df_arca_temp['Clave_Py'].duplicated(keep=False)])
    df_arca_temp['Clave_Unica'] = df_arca_temp['Clave_Py'] + "_" + df_arca_temp.groupby('Clave_Py').cumcount().astype(str)

    def normalize_comp_standard(x):
        s = str(x).strip().upper()
        match = re.search(r'([A-Z])[-\s]*(\d+)[-\s]*(\d+)', s)
        if match: return f"{match.group(1)}-{match.group(2).zfill(5)}-{match.group(3).zfill(8)}"
        return s

    # 2. CARGA INTELIGENTE DE LIBRO IVA
    df_xubio_raw = pd.read_csv(ruta_xubio, header=None) if ruta_xubio.endswith('.csv') else pd.read_excel(ruta_xubio, header=None)
    
    header_idx = 0
    val_a1 = str(df_xubio_raw.iloc[0, 0]).strip().lower() if not df_xubio_raw.empty else ""
    
    if "fecha desde" in val_a1:
        header_idx = 5
    else:
        for i, r in df_xubio_raw.head(15).iterrows():
            r_strs = [str(x).lower() for x in r.values]
            if any('comprobante' in x for x in r_strs) or any('fecha' in x for x in r_strs):
                header_idx = i
                break
                
    df_xubio = pd.read_csv(ruta_xubio, skiprows=header_idx) if ruta_xubio.endswith('.csv') else pd.read_excel(ruta_xubio, skiprows=header_idx)
    
    for i in range(22):
        if i >= len(df_xubio.columns):
            df_xubio[f"Col_Extra_{i}"] = np.nan
    
    nuevas_cols = list(df_xubio.columns)
    nuevas_cols[0:22] = ['Fecha', 'Tipo', 'Comprobante', 'Proveedor', 'CUIT', 'Condición IVA',
                         'Neto gravado 21%', 'IVA 21%', 'Neto gravado 10.5%', 'IVA 10.5%',
                         'Neto gravado 27%', 'IVA 27%', 'Neto gravado 2.5%', 'IVA 2.5%',
                         'Neto gravado 5%', 'IVA 5%', 'Retención IVA', 'Percepción IVA',
                         'Percepción IIBB', 'Impuestos internos', 'No gravado exento', 'Total']
    df_xubio.columns = nuevas_cols
    
    df_xubio.dropna(subset=['Tipo'], inplace=True)
    df_xubio['Comprobante'] = df_xubio['Comprobante'].apply(normalize_comp_standard)
    df_xubio['Condición IVA'] = df_xubio['Condición IVA'].astype(str).replace(r'^\s*RI\s*$', 'Responsable Inscripto', regex=True)

    for col in nuevas_cols[6:22]:
        df_xubio[col] = df_xubio[col].apply(parse_num)

    c_fec_x_clean = next((c for c in df_xubio.columns if 'fecha' in str(c).lower()), None)
    if c_fec_x_clean: 
        df_xubio.dropna(subset=[c_fec_x_clean], inplace=True)
        df_xubio = df_xubio[df_xubio[c_fec_x_clean].astype(str).str.strip() != '']
        df_xubio = df_xubio[~df_xubio[c_fec_x_clean].astype(str).str.upper().str.contains('TOTAL', na=False)]
        df_xubio[c_fec_x_clean] = df_xubio[c_fec_x_clean].apply(format_to_dd_mm_yyyy)

    df_xubio = df_xubio[df_xubio['Comprobante'].astype(str).str.strip() != '']
    df_xubio = df_xubio[~df_xubio['Comprobante'].astype(str).str.upper().str.contains('NAN', na=False)]
    df_xubio = df_xubio[~df_xubio['Comprobante'].astype(str).str.upper().str.contains('NONE', na=False)]
    df_xubio = df_xubio[~df_xubio['Comprobante'].astype(str).str.upper().str.contains('TOTAL', na=False)]

    c_tipo_mask = next((c for c in df_xubio.columns if 'tipo' in str(c).lower() and 'cambio' not in str(c).lower()), None)
    if c_tipo_mask:
        mask_no_extracto = ~df_xubio[c_tipo_mask].astype(str).str.lower().str.contains('extracto', na=False)
        dups_x = set(df_xubio[mask_no_extracto]['Comprobante'][df_xubio[mask_no_extracto]['Comprobante'].duplicated(keep=False)])
    else:
        dups_x = set(df_xubio['Comprobante'][df_xubio['Comprobante'].duplicated(keep=False)])

    all_dups = dups_a.union(dups_x)
    df_xubio['Clave_Unica'] = df_xubio['Comprobante'] + "_" + df_xubio.groupby('Comprobante').cumcount().astype(str)

    # 3. IDENTIFICACIÓN DE FALTANTES
    df_faltantes_arca = df_arca_temp[(~df_arca_temp['Clave_Unica'].isin(df_xubio['Clave_Unica'])) & (~df_arca_temp['Clave_Py'].isin(all_dups))].copy()
    df_faltantes_xubio = df_xubio[(~df_xubio['Clave_Unica'].isin(df_arca_temp['Clave_Unica'])) & (~df_xubio['Comprobante'].isin(all_dups))].copy()

    df_faltantes_usd_original = pd.DataFrame()
    col_tc_faltantes = [c for c in df_faltantes_arca.columns if 'tipo cambio' in str(c).lower()]
    if col_tc_faltantes:
        tc_col = col_tc_faltantes[0]
        tc_values = pd.to_numeric(df_faltantes_arca[tc_col], errors='coerce').fillna(1)
        mask_usd = tc_values > 1
        if mask_usd.any():
            df_faltantes_usd_original = df_faltantes_arca[mask_usd].drop(columns=['Clave_Py', 'Clave_Unica', 'Clave_Conciliacion'], errors='ignore').copy()
            money_cols_names = [c for c in df_faltantes_arca.columns if any(kw in str(c).lower() for kw in ['imp.', 'iva', 'total', 'gravado', 'exenta', 'exento', 'tributo', 'monto', 'neto'])]
            for c in money_cols_names:
                df_faltantes_arca.loc[mask_usd, c] = pd.to_numeric(df_faltantes_arca.loc[mask_usd, c], errors='coerce').fillna(0) * tc_values[mask_usd]

    # --- LISTA INTELIGENTE DE TASAS ---
    tasas = [
        {'a_keys': ['21%'], 'x_n': 'Neto gravado 21%', 'x_i': 'IVA 21%'},
        {'a_keys': ['10,5%', '10.5%'], 'x_n': 'Neto gravado 10.5%', 'x_i': 'IVA 10.5%'},
        {'a_keys': ['27%'], 'x_n': 'Neto gravado 27%', 'x_i': 'IVA 27%'},
        {'a_keys': ['2,5%', '2.5%'], 'x_n': 'Neto gravado 2.5%', 'x_i': 'IVA 2.5%'},
        {'a_keys': [' 5%', '05%'], 'x_n': 'Neto gravado 5%', 'x_i': 'IVA 5%'}
    ]

    # 4. XUBIO -> ARCA (Inyectar)
    def inyectar_hacia_arca(row):
        nueva_fila = {c: np.nan for c in df_arca.columns}
        tipo_xubio = str(row.get('Tipo_Formateado', row.get('Tipo', ''))).lower()
        
        es_nota_credito = bool(re.search(r'\bnc\b', tipo_xubio) or (('crédito' in tipo_xubio or 'credito' in tipo_xubio) and 'factura' not in tipo_xubio))
        es_nota_debito = bool(re.search(r'\bnd\b', tipo_xubio) or 'débito' in tipo_xubio or 'debito' in tipo_xubio)
        signo_nc = -1 if es_nota_credito else 1
        
        nueva_fila['Clave_Conciliacion'] = row.get('Comprobante', '')
        if arca_map['fecha']: nueva_fila[arca_map['fecha']] = row.get('Fecha', '')
        
        if arca_map['tipo']: 
            if es_nota_credito: nueva_fila[arca_map['tipo']] = 'Nota de Crédito'
            elif es_nota_debito: nueva_fila[arca_map['tipo']] = 'Nota de Débito'
            elif re.search(r'\bfc\b', tipo_xubio) or 'factura' in tipo_xubio: nueva_fila[arca_map['tipo']] = 'Factura'
            else: nueva_fila[arca_map['tipo']] = row.get('Tipo', '')
        
        comp_str = str(row.get('Comprobante', ''))
        if '-' in comp_str:
            parts = comp_str.split('-')
            if len(parts) == 3:
                if arca_map['pto']: nueva_fila[arca_map['pto']] = parts[1]
                if arca_map['num']: nueva_fila[arca_map['num']] = parts[2]
        
        if arca_map['prov']: 
            pk = 'Proveedor' if 'Proveedor' in row else ('Cliente' if 'Cliente' in row else 'Denominación Emisor')
            nueva_fila[arca_map['prov']] = row.get(pk, '')
            
        if arca_map['cuit']: 
            dk = 'CUIT' if 'CUIT' in row else ('Documento' if 'Documento' in row else 'Nro. Doc. Emisor')
            nueva_fila[arca_map['cuit']] = row.get(dk, '')
            
        if arca_map['tot']: nueva_fila[arca_map['tot']] = 0
        
        if arca_map['neto']:
            netos = [parse_num(row.get(c)) for c in row.keys() if 'neto' in str(c).lower()]
            nueva_fila[arca_map['neto']] = abs(sum(netos)) * signo_nc
                
        if arca_map['exe']:
            ek = next((k for k in row.keys() if 'exent' in str(k).lower() or 'no gravado' in str(k).lower()), 'Exento')
            nueva_fila[arca_map['exe']] = abs(parse_num(row.get(ek))) * signo_nc
                
        if arca_map['iva']:
            ivas = [parse_num(row.get(c)) for c in row.keys() if 'iva' in str(c).lower() and '%' in str(c)]
            nueva_fila[arca_map['iva']] = abs(sum(ivas)) * signo_nc
            
        if arca_map['trib']:
            nt = ['perc. iva', 'ret. iva', 'perc. iibb', 'impuestos internos', 'otras perc', 'retención iva', 'percepción iva', 'percepción iibb']
            tribs = [parse_num(row.get(c)) for c in row.keys() if str(c).lower().strip() in nt]
            nueva_fila[arca_map['trib']] = abs(sum(tribs)) * signo_nc
            
        for t in tasas:
            n_val = parse_num(row.get(t['x_n']))
            i_val = parse_num(row.get(t['x_i']))
            
            c_na = next((c for c in df_arca.columns if 'neto' in str(c).lower() and 'total' not in str(c).lower() and any(k in str(c).lower() for k in t['a_keys'])), None)
            c_ia = next((c for c in df_arca.columns if 'iva' in str(c).lower() and 'neto' not in str(c).lower() and 'total' not in str(c).lower() and any(k in str(c).lower() for k in t['a_keys'])), None)
            
            if c_na: nueva_fila[c_na] = abs(n_val) * signo_nc
            if c_ia: nueva_fila[c_ia] = abs(i_val) * signo_nc

        return nueva_fila

    importados_a_list = [inyectar_hacia_arca(row) for _, row in df_faltantes_xubio.iterrows()]
    df_importados_a = pd.DataFrame(importados_a_list) if importados_a_list else pd.DataFrame(columns=df_arca.columns)
    df_importados_a.dropna(how='all', inplace=True)

    # 5. ARCA -> XUBIO (Inyectar)
    def inyectar_hacia_xubio(row):
        nueva_fila = {c: np.nan for c in df_xubio.columns}
        tipo_arca = str(row.get(arca_map['tipo'], '')).lower()
        
        # Filtro inteligente: Es Nota de Crédito SOLO si dice crédito y NO dice factura
        es_nota_credito_arca = ('crédito' in tipo_arca or 'credito' in tipo_arca) and 'factura' not in tipo_arca
        signo = -1 if es_nota_credito_arca else 1
        
        for c in df_xubio.columns:
            cl = str(c).lower()
            if 'comprobante' in cl: nueva_fila[c] = row.get('Clave_Py', '')
            if 'fecha' in cl:
                v_fec = row.get(arca_map['fecha'], '') if arca_map['fecha'] else row.get('Fecha', '')
                nueva_fila[c] = v_fec if pd.notna(v_fec) else ''
            if 'tipo' in cl and 'cambio' not in cl:
                if es_nota_credito_arca: nueva_fila[c] = 'Nota Crédito'
                elif 'débito' in tipo_arca or 'debito' in tipo_arca: nueva_fila[c] = 'Nota Débito'
                elif 'factura' in tipo_arca: nueva_fila[c] = 'Factura'
                else: 
                    v_tipo = row.get(arca_map['tipo'], '') if arca_map['tipo'] else ''
                    nueva_fila[c] = v_tipo if pd.notna(v_tipo) else ''
            if 'proveedor' in cl or 'cliente' in cl or 'denominaci' in cl or 'razón' in cl or 'razon' in cl:
                v_prov = row.get(arca_map['prov'], '') if arca_map['prov'] else row.get('Denominación Emisor', '')
                nueva_fila[c] = v_prov if pd.notna(v_prov) else ''
            if 'cuit' in cl or 'documento' in cl:
                v_cuit = row.get(arca_map['cuit']) if arca_map['cuit'] else row.get('Nro. Doc. Emisor', '')
                nueva_fila[c] = v_cuit if pd.notna(v_cuit) else ''
        
        c_tot_x = next((c for c in df_xubio.columns if 'total' in str(c).lower() and 'iva' not in str(c).lower()), None)
        if c_tot_x:
            c_tot_a = next((c for c in df_arca.columns if 'imp. total' in str(c).lower()), 'Imp. Total')
            nueva_fila[c_tot_x] = parse_num(row.get(c_tot_a)) * signo
            
        c_neto_x_total = next((c for c in df_xubio.columns if 'neto' in str(c).lower() and '%' not in str(c).lower() and '21' not in str(c) and '10' not in str(c) and '27' not in str(c)), None)
        if c_neto_x_total: nueva_fila[c_neto_x_total] = parse_num(row.get(arca_map['neto'])) * signo

        c_exe = next((c for c in df_xubio.columns if 'exent' in str(c).lower() or 'no gravado' in str(c).lower()), None)
        if c_exe: nueva_fila[c_exe] = parse_num(row.get(arca_map['exe'])) * signo
            
        c_trib = next((c for c in df_xubio.columns if 'percepción iva' in str(c).lower() or 'percepcion iva' in str(c).lower()), None)
        if c_trib: nueva_fila[c_trib] = parse_num(row.get(arca_map['trib'])) * signo
                
        for t in tasas:
            c_na = next((c for c in df_arca.columns if 'neto' in str(c).lower() and 'total' not in str(c).lower() and any(k in str(c).lower() for k in t['a_keys'])), None)
            c_ia = next((c for c in df_arca.columns if 'iva' in str(c).lower() and 'neto' not in str(c).lower() and 'total' not in str(c).lower() and any(k in str(c).lower() for k in t['a_keys'])), None)
            
            n_val = parse_num(row.get(c_na)) if c_na else 0.0
            i_val = parse_num(row.get(c_ia)) if c_ia else 0.0
            
            if t['x_n'] in df_xubio.columns: nueva_fila[t['x_n']] = n_val * signo
            if t['x_i'] in df_xubio.columns: nueva_fila[t['x_i']] = i_val * signo
                
        return nueva_fila

    importados_x_list = [inyectar_hacia_xubio(row) for _, row in df_faltantes_arca.iterrows()]
    df_importados_x = pd.DataFrame(importados_x_list) if importados_x_list else pd.DataFrame(columns=df_xubio.columns)
    df_importados_x.dropna(how='all', inplace=True)

    df_faltantes_arca.drop(columns=['Clave_Py', 'Clave_Unica', 'Clave_Conciliacion'], inplace=True, errors='ignore')

    original_max_row_arca = len(df_arca) + 1
    original_max_row_xubio = len(df_xubio) + 1

    if not df_importados_a.empty:
        df_vacia = pd.DataFrame([[np.nan]*len(df_arca.columns)], columns=df_arca.columns)
        df_vacia['Clave_Conciliacion'] = "---"
        df_titulo = pd.DataFrame([[np.nan]*len(df_arca.columns)], columns=df_arca.columns)
        df_titulo['Clave_Conciliacion'] = "---TITULO---"
        df_encabezados = pd.DataFrame([[np.nan]*len(df_arca.columns)], columns=df_arca.columns)
        df_encabezados['Clave_Conciliacion'] = "---ENCABEZADOS---"
        df_arca = pd.concat([df_arca, df_vacia, df_titulo, df_encabezados, df_importados_a], ignore_index=True)
    df_arca['Clave_Conciliacion'] = df_arca['Clave_Conciliacion'].fillna("")

    if not df_importados_x.empty:
        col_0_x = df_xubio.columns[0]
        df_vacia_x = pd.DataFrame([[np.nan]*len(df_xubio.columns)], columns=df_xubio.columns)
        df_vacia_x[col_0_x] = "---"
        df_titulo_x = pd.DataFrame([[np.nan]*len(df_xubio.columns)], columns=df_xubio.columns)
        df_titulo_x[col_0_x] = "---TITULO2---"
        df_encabezados_x = pd.DataFrame([[np.nan]*len(df_xubio.columns)], columns=df_xubio.columns)
        df_encabezados_x[col_0_x] = "---ENCABEZADOS2---"
        df_xubio = pd.concat([df_xubio, df_vacia_x, df_titulo_x, df_encabezados_x, df_importados_x], ignore_index=True)
        df_xubio[col_0_x] = df_xubio[col_0_x].fillna("")

    # 6. ESCRITURA EN EXCEL
    wb = Workbook()
    bold_font = Font(bold=True)
    right_align = Alignment(horizontal='right')
    orange_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")
    yellow_fill = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
    light_red_fill = PatternFill(start_color="FFCCCC", end_color="FFCCCC", fill_type="solid")
    injected_fill = PatternFill(start_color="FCE4D6", end_color="FCE4D6", fill_type="solid")

    def get_col_letter_by_name(ws, name):
        for col in range(1, ws.max_column + 1):
            val = str(ws.cell(1, col).value).lower().strip()
            if name.lower() in val and len(val) <= len(name) + 2: return get_column_letter(col)
        return None
    
    def get_money_cols(ws):
        cols = []
        for c in range(1, ws.max_column + 1):
            val = ws.cell(1, c).value
            if val:
                header = str(val).lower()
                if any(kw in header for kw in ['imp.', 'iva', 'total', 'gravado', 'exenta', 'exento', 'tributo', 'ajustado', 'diferencia', 'monto', 'neto']):
                    cols.append(c)
        return cols

    # --- HOJA ARCA ---
    ws_arca = wb.active
    ws_arca.title = "Mis Comprobantes"
    for r in dataframe_to_rows(df_arca, index=False, header=True): ws_arca.append(r)

    col_tipo_arca = get_col_letter_by_name(ws_arca, 'tipo')
    col_pto_arca = get_col_letter_by_name(ws_arca, 'punto de venta')
    col_num_arca = get_col_letter_by_name(ws_arca, 'número desde')
    col_iva_arca = get_col_letter_by_name(ws_arca, 'total iva') or get_col_letter_by_name(ws_arca, 'iva')
    col_tot_arca = get_col_letter_by_name(ws_arca, 'imp. total')
    col_tc_arca = get_col_letter_by_name(ws_arca, 'tipo cambio')

    idx_iva_ajust = ws_arca.max_column + 1
    idx_tot_ajust = ws_arca.max_column + 2
    ws_arca.cell(1, idx_iva_ajust, "IVA Ajustado").fill = yellow_fill
    ws_arca.cell(1, idx_tot_ajust, "Total Ajustado").fill = yellow_fill
    letra_iva_ajust_arca = get_column_letter(idx_iva_ajust)
    letra_tot_ajust_arca = get_column_letter(idx_tot_ajust)

    max_row_arca = ws_arca.max_row
    fila_vacia_arca = None
    fila_titulo_arca = None

    for row in range(2, max_row_arca + 1):
        val_a = str(ws_arca[f"A{row}"].value or "").strip()
        
        if val_a == "---":
            for c in range(1, ws_arca.max_column + 1): ws_arca.cell(row, c).value = ""
            fila_vacia_arca = row
            continue 
        
        if val_a == "---TITULO---":
            for c in range(1, ws_arca.max_column + 1): ws_arca.cell(row, c).value = ""
            ws_arca[f"A{row}"].value = f"Partidas del Libro IVA {operacion} no encontradas en ARCA"
            ws_arca[f"A{row}"].font = bold_font
            fila_titulo_arca = row
            continue
            
        if val_a == "---ENCABEZADOS---":
            for c in range(1, ws_arca.max_column + 1):
                let_c = get_column_letter(c)
                ws_arca.cell(row, c).value = f"={let_c}1"
                ws_arca.cell(row, c).font = bold_font
                if c != idx_iva_ajust and c != idx_tot_ajust: ws_arca.cell(row=row, column=c).fill = injected_fill
            ws_arca[f"{letra_iva_ajust_arca}{row}"].fill = yellow_fill
            ws_arca[f"{letra_tot_ajust_arca}{row}"].fill = yellow_fill
            continue

        if fila_titulo_arca and row > fila_titulo_arca:
            for c in range(1, ws_arca.max_column + 1):
                if c != idx_iva_ajust and c != idx_tot_ajust: ws_arca.cell(row=row, column=c).fill = injected_fill
            if col_tot_arca:
                col_start_sum = "Z" if operacion == "Compras" else "X"
                col_end_sum = "AD" if operacion == "Compras" else "AB"
                ws_arca[f"{col_tot_arca}{row}"].value = f"=SUM({col_start_sum}{row}:{col_end_sum}{row})"

        if val_a != "" and val_a in all_dups:
            for c in range(1, ws_arca.max_column + 1):
                if c != idx_iva_ajust and c != idx_tot_ajust: ws_arca.cell(row=row, column=c).fill = light_red_fill

        ws_arca[f"{letra_iva_ajust_arca}{row}"].fill = yellow_fill
        ws_arca[f"{letra_tot_ajust_arca}{row}"].fill = yellow_fill

        if val_a == "":
            if col_tipo_arca and col_pto_arca and col_num_arca:
                ws_arca[f"A{row}"].value = f'=RIGHT({col_tipo_arca}{row}, 1)&"-"&TEXT({col_pto_arca}{row}, "00000")&"-"&TEXT({col_num_arca}{row}, "00000000")'
        
        tc_mult = f" * IF(AND(ISNUMBER({col_tc_arca}{row}), {col_tc_arca}{row}>1), {col_tc_arca}{row}, 1)" if col_tc_arca else ""
        signo = f'IF(AND(OR(ISNUMBER(SEARCH("Crédito", {col_tipo_arca}{row})), ISNUMBER(SEARCH("Credito", {col_tipo_arca}{row}))), ISERR(SEARCH("Factura", {col_tipo_arca}{row}))), -1, 1)'
        
        if col_iva_arca: ws_arca[f"{letra_iva_ajust_arca}{row}"].value = f'=IF(ISNUMBER({col_iva_arca}{row}), ABS({col_iva_arca}{row}) * {signo}{tc_mult}, 0)'
        if col_tot_arca: ws_arca[f"{letra_tot_ajust_arca}{row}"].value = f'=IF(ISNUMBER({col_tot_arca}{row}), ABS({col_tot_arca}{row}) * {signo}{tc_mult}, 0)'

    # --- HOJA XUBIO ---
    ws_xubio = wb.create_sheet("Libro Iva")
    for r in dataframe_to_rows(df_xubio, index=False, header=True): ws_xubio.append(r)

    col_tipo_x = get_col_letter_by_name(ws_xubio, 'tipo')
    col_comp_x = get_col_letter_by_name(ws_xubio, 'comprobante')
    letra_tot_x = get_col_letter_by_name(ws_xubio, 'total')
    
    iva_cols_x = [get_column_letter(c) for c in range(1, ws_xubio.max_column + 1) if 'iva' in str(ws_xubio.cell(1, c).value).lower() and '%' in str(ws_xubio.cell(1, c).value)]

    idx_s_iva = ws_xubio.max_column + 1
    idx_s_tot = ws_xubio.max_column + 2
    idx_v_iva = ws_xubio.max_column + 3
    idx_v_tot = ws_xubio.max_column + 4
    idx_d_iva = ws_xubio.max_column + 5
    idx_d_tot = ws_xubio.max_column + 6
    idx_est = ws_xubio.max_column + 7

    titulos = ["TOTAL IVA S/LIBRO", "TOTAL MONTO S/LIBRO", "TOTAL IVA S/ARCA", "TOTAL MONTO S/ARCA", "DIFERENCIA IVA", "DIFERENCIA MONTO", "ESTADO CONCILIACIÓN"]
    for i, tit in enumerate(titulos): ws_xubio.cell(1, idx_s_iva + i, tit).fill = yellow_fill 

    letra_s_iva = get_column_letter(idx_s_iva)
    letra_s_tot = get_column_letter(idx_s_tot)
    letra_v_iva = get_column_letter(idx_v_iva)
    letra_v_tot = get_column_letter(idx_v_tot)
    letra_d_iva = get_column_letter(idx_d_iva)
    letra_d_tot = get_column_letter(idx_d_tot)
    letra_est = get_column_letter(idx_est)

    max_row_xubio = ws_xubio.max_row
    fila_vacia_xubio = None
    fila_titulo_xubio = None

    for row in range(2, max_row_xubio + 1):
        val_a = str(ws_xubio[f"A{row}"].value or "").strip() 
        val_c = str(ws_xubio[f"{col_comp_x}{row}"].value or "").strip() if col_comp_x else ""
        tipo_val = str(ws_xubio[f"{col_tipo_x}{row}"].value or "").lower() if col_tipo_x else ""
        
        is_dup = (val_c != "") and (val_c in all_dups) and ("extracto" not in tipo_val)
        
        if val_a == "---":
            for c in range(1, ws_xubio.max_column + 1): ws_xubio.cell(row, c).value = ""
            fila_vacia_xubio = row
            continue
        
        if val_a == "---TITULO2---":
            for c in range(1, ws_xubio.max_column + 1): ws_xubio.cell(row, c).value = ""
            ws_xubio[f"A{row}"].value = f"Partidas de ARCA no encontradas en el Libro IVA {operacion}"
            ws_xubio[f"A{row}"].font = bold_font
            fila_titulo_xubio = row
            continue
            
        if val_a == "---ENCABEZADOS2---":
            for c in range(1, ws_xubio.max_column + 1):
                let_c = get_column_letter(c)
                ws_xubio.cell(row, c).value = f"={let_c}1"
                ws_xubio.cell(row, c).font = bold_font
                if c < idx_s_iva: ws_xubio.cell(row=row, column=c).fill = injected_fill
                else: ws_xubio.cell(row=row, column=c).fill = yellow_fill
            continue

        if fila_titulo_xubio and row > fila_titulo_xubio:
            for c in range(1, idx_s_iva): ws_xubio.cell(row=row, column=c).fill = injected_fill
            for c in range(idx_s_iva, idx_est + 1): ws_xubio.cell(row=row, column=c).fill = yellow_fill
                
            # Conservamos los totales del lado del libro para poder ver la plata faltante
            suma_iva = f"=SUM({','.join([f'{c}{row}' for c in iva_cols_x])})" if iva_cols_x else "=0"
            ws_xubio[f"{letra_s_iva}{row}"].value = suma_iva
            ws_xubio[f"{letra_s_tot}{row}"].value = f"={letra_tot_x}{row}" if letra_tot_x else "0"
            
            # Limpiamos las columnas de la foto (ARCA y Diferencias) para que no repitan info ni den cero
            ws_xubio[f"{letra_v_iva}{row}"].value = ""
            ws_xubio[f"{letra_v_tot}{row}"].value = ""
            ws_xubio[f"{letra_d_iva}{row}"].value = ""
            ws_xubio[f"{letra_d_tot}{row}"].value = ""
            ws_xubio[f"{letra_est}{row}"].value = "Faltante en XUBIO"
            continue

        if is_dup:
            for c in range(1, idx_s_iva): ws_xubio.cell(row=row, column=c).fill = light_red_fill
        elif col_tipo_x and "extracto" in tipo_val:
            for c in range(1, idx_s_iva): ws_xubio.cell(row, c).fill = orange_fill
        
        for c in range(idx_s_iva, idx_est + 1): ws_xubio.cell(row, c).fill = yellow_fill

        suma_iva = f"=SUM({','.join([f'{c}{row}' for c in iva_cols_x])})" if iva_cols_x else "=0"
        ws_xubio[f"{letra_s_iva}{row}"].value = suma_iva
        ws_xubio[f"{letra_s_tot}{row}"].value = f"={letra_tot_x}{row}" if letra_tot_x else "0"
        
        if is_dup:
            ws_xubio[f"{letra_v_iva}{row}"].value = 0
            ws_xubio[f"{letra_v_tot}{row}"].value = 0
            ws_xubio[f"{letra_d_iva}{row}"].value = f'={letra_s_iva}{row} - {letra_v_iva}{row}'
            ws_xubio[f"{letra_d_tot}{row}"].value = f'={letra_s_tot}{row} - {letra_v_tot}{row}'
            ws_xubio[f"{letra_est}{row}"].value = "Duplicado"
        else:
            if col_comp_x:
                ws_xubio[f"{letra_v_iva}{row}"].value = f'=IFERROR(VLOOKUP({col_comp_x}{row}, \'Mis Comprobantes\'!$A$2:${letra_tot_ajust_arca}${original_max_row_arca}, {idx_iva_ajust}, 0), 0)'
                ws_xubio[f"{letra_v_tot}{row}"].value = f'=IFERROR(VLOOKUP({col_comp_x}{row}, \'Mis Comprobantes\'!$A$2:${letra_tot_ajust_arca}${original_max_row_arca}, {idx_tot_ajust}, 0), 0)'
            
            ws_xubio[f"{letra_d_iva}{row}"].value = f'={letra_s_iva}{row} - {letra_v_iva}{row}'
            ws_xubio[f"{letra_d_tot}{row}"].value = f'={letra_s_tot}{row} - {letra_v_tot}{row}'
            ws_xubio[f"{letra_est}{row}"].value = f'=IF(AND({letra_d_iva}{row}>=-0.02, {letra_d_iva}{row}<=0.02, {letra_d_tot}{row}>=-0.02, {letra_d_tot}{row}<=0.02), "Ok", "Faltante en ARCA")'

    regla_revisar = FormulaRule(formula=[f'${letra_est}2="Faltante en ARCA"'], font=Font(bold=True))
    ws_xubio.conditional_formatting.add(f"A2:{letra_est}{max_row_xubio}", regla_revisar)

    # --- TOTALES FINALES ---
    t_a1, t_a2, t_a3 = max_row_arca + 2, max_row_arca + 3, max_row_arca + 4
    t_x1, t_x2, t_x3 = max_row_xubio + 2, max_row_xubio + 3, max_row_xubio + 4

    col_label_arca = get_column_letter(max(1, idx_iva_ajust - 1))
    col_label_xubio = get_column_letter(max(1, idx_s_iva - 1))
    ws_xubio.column_dimensions[col_label_xubio].hidden = False

    ws_arca[f"{col_label_arca}{t_a1}"] = "TOTALES ARCA"
    ws_arca[f"{col_label_arca}{t_a2}"] = f"TOTALES LIBRO IVA {operacion.upper()}"
    ws_arca[f"{col_label_arca}{t_a3}"] = "DIFERENCIA"
    
    ws_xubio[f"{col_label_xubio}{t_x1}"] = f"TOTALES LIBRO IVA {operacion.upper()}"
    ws_xubio[f"{col_label_xubio}{t_x2}"] = "TOTALES ARCA"
    ws_xubio[f"{col_label_xubio}{t_x3}"] = "DIFERENCIA"

    for row in [t_a1, t_a2, t_a3]: 
        ws_arca[f"{col_label_arca}{row}"].font = bold_font
        ws_arca[f"{col_label_arca}{row}"].alignment = right_align
    for row in [t_x1, t_x2, t_x3]: 
        ws_xubio[f"{col_label_xubio}{row}"].font = bold_font
        ws_xubio[f"{col_label_xubio}{row}"].alignment = right_align

    ws_arca[f"{letra_iva_ajust_arca}{t_a1}"].value = f"=SUM({letra_iva_ajust_arca}2:{letra_iva_ajust_arca}{max_row_arca})"
    ws_arca[f"{letra_tot_ajust_arca}{t_a1}"].value = f"=SUM({letra_tot_ajust_arca}2:{letra_tot_ajust_arca}{max_row_arca})"

    if letra_tot_x: ws_xubio[f"{letra_tot_x}{t_x1}"].value = f"=SUM({letra_tot_x}2:{letra_tot_x}{max_row_xubio})"
    for c_idx in range(idx_s_iva, idx_est):
        let = get_column_letter(c_idx)
        ws_xubio[f"{let}{t_x1}"].value = f"=SUM({let}2:{let}{max_row_xubio})"

    if letra_tot_ajust_arca and letra_tot_x:
        ws_arca[f"{letra_tot_ajust_arca}{t_a2}"].value = f"='Libro Iva'!{letra_tot_x}{t_x1}"
        ws_arca[f"{letra_tot_ajust_arca}{t_a3}"].value = f"={letra_tot_ajust_arca}{t_a1} - {letra_tot_ajust_arca}{t_a2}"
        ws_xubio[f"{letra_s_tot}{t_x2}"].value = f"='Mis Comprobantes'!{letra_tot_ajust_arca}{t_a1}"
        ws_xubio[f"{letra_s_tot}{t_x3}"].value = f"={letra_s_tot}{t_x1} - {letra_s_tot}{t_x2}"
    
    if letra_iva_ajust_arca and letra_s_iva:
        ws_arca[f"{letra_iva_ajust_arca}{t_a2}"].value = f"='Libro Iva'!{letra_s_iva}{t_x1}"
        ws_arca[f"{letra_iva_ajust_arca}{t_a3}"].value = f"={letra_iva_ajust_arca}{t_a1} - {letra_iva_ajust_arca}{t_a2}"
        ws_xubio[f"{letra_s_iva}{t_x2}"].value = f"='Mis Comprobantes'!{letra_iva_ajust_arca}{t_a1}"
        ws_xubio[f"{letra_s_iva}{t_x3}"].value = f"={letra_s_iva}{t_x1} - {letra_s_iva}{t_x2}"

    # --- HOJA FALTANTES (OPCIONAL EN ARCA) ---
    ws_faltantes = wb.create_sheet(f"Faltantes en Libro IVA {operacion}")
    for r in dataframe_to_rows(df_faltantes_arca, index=False, header=True): ws_faltantes.append(r)
    
    if not df_faltantes_usd_original.empty:
        start_row_usd = ws_faltantes.max_row + 5
        ws_faltantes.cell(row=start_row_usd, column=1, value="Facturas Originales en USD").font = bold_font
        header_row = start_row_usd + 1
        for col_idx, col_name in enumerate(df_faltantes_usd_original.columns, 1): ws_faltantes.cell(row=header_row, column=col_idx, value=col_name).font = bold_font
        for r_idx, r_data in enumerate(dataframe_to_rows(df_faltantes_usd_original, index=False, header=False), header_row + 1):
            for c_idx, val in enumerate(r_data, 1): ws_faltantes.cell(row=r_idx, column=c_idx, value=val)

    # --- ESTILOS DE MONEDA Y TAMAÑO DE COLUMNAS ---
    money_cols_arca = get_money_cols(ws_arca)
    money_cols_xubio = get_money_cols(ws_xubio)

    def aplicar_moneda(ws, max_data_row, max_total_row, cols_monto, rows_to_skip=None, fila_tit_xubio=None, idx_v_iva=None):
        if rows_to_skip is None: rows_to_skip = []
        for r in range(2, ws.max_row + 1):
            if r in rows_to_skip or r == max_data_row + 1: continue 
            val1 = str(ws.cell(r, 1).value or "").strip()
            if val1 in ["Facturas Originales en USD", f"Partidas de ARCA no encontradas en el Libro IVA {operacion}", f"Partidas del Libro IVA {operacion} no encontradas en ARCA"]: continue
            if isinstance(val1, str) and val1.startswith('=A1'): continue

            for c in cols_monto:
                celda = ws.cell(r, c)
                
                # Para filas inyectadas en Xubio, dejamos verdaderamente en blanco las de ARCA y Diferencias
                if ws.title == "Libro Iva" and fila_tit_xubio and r > fila_tit_xubio and idx_v_iva and c >= idx_v_iva and c < idx_v_iva + 4:
                    celda.value = ""
                    continue
                    
                if not isinstance(celda.value, str) or not celda.value.startswith('='):
                    if celda.value is None or celda.value == "" or (isinstance(celda.value, float) and np.isnan(celda.value)):
                        if r > max_data_row and r <= max_total_row: continue
                        celda.value = 0
                celda.number_format = '#,##0.00;[Red]-#,##0.00;"-"'

    filas_a_saltar = [r for r in [fila_vacia_arca, fila_titulo_arca] if r is not None]
    filas_a_saltar_xubio = [r for r in [fila_vacia_xubio, fila_titulo_xubio] if r is not None]
    
    aplicar_moneda(ws_arca, max_row_arca, t_a3, money_cols_arca, filas_a_saltar)
    aplicar_moneda(ws_xubio, max_row_xubio, t_x3, money_cols_xubio, filas_a_saltar_xubio, fila_titulo_xubio, idx_v_iva)
    aplicar_moneda(ws_faltantes, ws_faltantes.max_row, ws_faltantes.max_row, get_money_cols(ws_faltantes))

    for col_to_hide in ['D', 'E', 'F', 'G', 'H', 'L', 'K']: ws_arca.column_dimensions[col_to_hide].hidden = True
    for col_to_hide in ['E', 'S', 'T', 'U']: ws_xubio.column_dimensions[col_to_hide].hidden = True

    ws_arca.freeze_panes = "B2"
    ws_xubio.freeze_panes = "B2"
    ws_faltantes.freeze_panes = "B2"

    for ws in [ws_arca, ws_xubio, ws_faltantes]:
        ws.sheet_view.zoomScale = 80
        for col in range(1, ws.max_column + 1):
            letter = get_column_letter(col)
            header_val = str(ws.cell(1, col).value or "").lower()
            if 'denominaci' in header_val or 'proveedor' in header_val: ws.column_dimensions[letter].width = 40
            else:
                max_length = 0
                for row in range(1, ws.max_row + 1):
                    cell_val = ws.cell(row=row, column=col).value
                    if cell_val is not None:
                        str_val = str(cell_val)
                        if str_val in [f"Partidas del Libro IVA {operacion} no encontradas en ARCA", "Facturas Originales en USD", f"Partidas de ARCA no encontradas en el Libro IVA {operacion}"]: continue
                        length = max(len(header_val), 12) if str_val.startswith('=') else len(str_val)
                        if length > max_length: max_length = length
                adjusted_width = max_length + 2
                if adjusted_width < 10: adjusted_width = 10
                if adjusted_width > 50: adjusted_width = 50
                ws.column_dimensions[letter].width = adjusted_width

    wb.save(ruta_salida)
    log(f"      [OK] Control de facturas generado: {ruta_salida}")
    return ruta_salida
