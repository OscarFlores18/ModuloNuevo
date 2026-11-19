"""Genera assets/icono.ico y assets/icono.png (el ícono de la app).

    python assets/generar_icono.py assets/icono

Necesita Pillow (pip install pillow); solo para regenerar el ícono, no para correr la app.
"""
import sys
from PIL import Image, ImageDraw

S = 1024
def lerp(a, b, t): return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))

# Fondo: cuadrado redondeado con degradé azul (los colores de la app).
fondo = Image.new("RGB", (S, S))
px = fondo.load()
c1, c2 = (59, 130, 246), (30, 58, 138)
for y in range(S):
    for x in range(S):
        px[x, y] = lerp(c1, c2, (x + y) / (2 * S))
mascara = Image.new("L", (S, S), 0)
ImageDraw.Draw(mascara).rounded_rectangle((0, 0, S - 1, S - 1), radius=220, fill=255)
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
img.paste(fondo, (0, 0), mascara)
d = ImageDraw.Draw(img)

# Hoja (factura) con la esquina doblada.
x0, y0, x1, y1, dob = 250, 170, 720, 830, 150
d.polygon([(x0, y0), (x1 - dob, y0), (x1, y0 + dob), (x1, y1), (x0, y1)], fill=(255, 255, 255))
d.polygon([(x1 - dob, y0), (x1 - dob, y0 + dob), (x1, y0 + dob)], fill=(191, 219, 254))
# Renglones.
for i, ancho in enumerate((330, 260, 330, 200)):
    y = 380 + i * 95
    d.rounded_rectangle((x0 + 70, y, x0 + 70 + ancho, y + 38), radius=19, fill=(148, 163, 184))

# Tilde verde (control OK).
cx, cy, r = 700, 760, 175
d.ellipse((cx - r - 22, cy - r - 22, cx + r + 22, cy + r + 22), fill=(30, 58, 138))
d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(22, 163, 74))
d.line([(cx - 85, cy + 5), (cx - 22, cy + 70), (cx + 95, cy - 65)], fill=(255, 255, 255), width=58, joint="curve")

destino = sys.argv[1]
img.save(destino + ".png")
img.save(destino + ".ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("ok")
