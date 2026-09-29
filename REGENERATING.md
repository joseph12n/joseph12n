# Regenerar los assets

Los SVG de `assets/` están commiteados: GitHub no ejecuta nada, los muestra.
Para cambiarlos hay que correr los scripts de `scripts/` en tu máquina y
subir el resultado.

## Preparación

```bash
pip install -r scripts/banner/requirements.txt   # numpy, scipy, Pillow
pip install cairosvg                              # SVG -> PNG, para los logos
```

`rembg` hace falta solo para recortar la foto, no para generar el banner.

## Los radares (automático)

`charts.yml` los redibuja solo cuando tocás `assets/skills.json` y de noche.
Si los querés ver ya:

```bash
python scripts/radar.py --data assets/skills.json -o assets/radar --values
python scripts/radar.py --github joseph12n -o assets/radar-langs --limit 6 --values \
    --title "joseph12n · language mix"
```

## Calificar los ejes del radar

```bash
python scripts/radar.py --rate
```

Pregunta los 6 valores de 0 a 100; Enter deja el actual. Para cambiar los
nombres de los ejes:

```bash
python scripts/radar.py --rate --axes "Backend Java,Frontend React,QA,Data,Mobile,DevOps"
```

Ese comando escribe `skills.json` y redibuja los radares. **La tarjeta
`whoami-ember.svg` tiene los mismos seis números** y hay que redibujarla aparte:

```bash
python scripts/whoami/render.py
```

`charts.yml` hace las dos cosas, así que un `git push` con el JSON nuevo basta.
Localmente, si corrés `--rate` a mano, acordate de correr también el de la
tarjeta. Antes de que existiera ese script los dos mostraban números distintos.

## El banner

La foto está en `.gitignore`, así que este paso es local y no corre en CI.

```bash
# 1. recortar la persona y encuadrar a la retícula 300x340  (requiere rembg)
pip install rembg
python scripts/banner/prepare_portrait.py assets/source/portrait.jpg

# 2. generar
python scripts/banner/generate.py
```

Antes de generar los 870 KiB de cada tema, conviene iterar barato:

```bash
python scripts/banner/generate.py --preview   # solo la retícula ditherizada
python scripts/banner/still.py                # el frame del retrato
python scripts/banner/still.py linux          # el frame de un logo
```

Ajustes por variable de entorno, sin tocar el código:

| Variable | Por defecto | Para qué |
|---|---|---|
| `TRAVELLERS` | 600 | partículas en movimiento (900+ pesa ~910 KiB) |
| `HOLD_PARTICLES` | 2600 | densidad de la silueta en cada hold |
| `LOGO_SCALE` | 0.90 | tamaño del logo dentro del visor |
| `CROP_W`, `CROP_TOP`, `CROP_X` | 1.0, 0.0, 0.0 | recorte, para fotos sin encuadrar |
| `PORTRAIT` | `assets/source/portrait.png` | usar otra imagen |

## Los iconos de la tabla

```bash
python scripts/icons/fetch_icons.py          # descarga a assets/icons/
python scripts/icons/fetch_icons.py --table  # imprime el marcado del README
```

Cada ícono prueba sus fuentes en orden y valida la respuesta antes de
aceptarla. Importa, porque `skillicons.dev` devuelve HTTP 200 con un SVG
vacío para los slugs que no tiene: sin validación, el fallo aparece como una
celda en blanco y no como un error.

Para agregar una herramienta, edita `STACK` en `scripts/icons/fetch_icons.py`
y corre los dos comandos. Si ninguna fuente tiene el ícono, el scraper lo
reporta y sale con código 1.

## Qué NO se regenera

`assets/banner-*.svg` necesita `assets/source/portrait.jpg`, que está en
`.gitignore`, así que ese paso nunca corre en CI. El resto del pipeline sí.

`assets/source/anime-src.png` se usa tal cual, sin procesar.

## Un detalle sobre los SVG animados

El banner usa SMIL, y los rasterizadores estáticos (cairosvg, ImageMagick,
la vista previa de muchos editores) solo muestran el **primer frame**. Para
ver un frame concreto de la animación:

```bash
python scripts/banner/still.py          # tu cara
python scripts/banner/still.py linux    # el hold de Tux
python scripts/banner/still.py go       # el hold del gopher
```

Para el movimiento real hay que abrirlo en un navegador.
