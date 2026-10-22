# 🦸‍♂️ Agente Monitor 24/7 de Restock — Tienda Panini México (Marvel & DC)

Agente ultraligero escrito 100% con la librería estándar de Python (**cero dependencias externas**, `<15 MB` de RAM) que consulta directamente la API **Magento 2 GraphQL** oficial de `https://tiendapanini.com.mx` para las categorías:
- **Marvel Comics** (`category_id: 1533`)
- **DC Comics** (`category_id: 516`)

Cada vez que un cómic, tomo recopilatorio, *Marvel Must Have*, *Omnibus* o *DC Pocket* que estaba **Agotado (`OUT_OF_STOCK`)** vuelve a estar **Disponible (`IN_STOCK`)**, el agente envía una alerta instantánea con título, editorial, SKU, precio en MXN, portada y enlace directo de compra.

---

## 📂 Estructura del Proyecto

- `monitor.py` — Agente principal (modo continuo 24/7 con servidor Keep-Alive HTTP integrado o modo ejecución única `--once`).
- `.env.example` — Plantilla de variables de entorno (Telegram, Discord, ntfy.sh, filtros por palabras clave).
- `.github/workflows/panini_monitor.yml` — Automatización Serverless 24/7 en **GitHub Actions** (100% gratis, sin servidor propio).
- `Dockerfile` — Imagen ultraligera basada en `python:3.12-alpine` para contenedores gratuitos.
- `render.yaml` — Despliegue en 1 clic para **Render.com (Free Tier)** / **Koyeb**.

---

## 🔔 Paso 1: Configurar tu Canal de Alertas (Elige al menos uno)

### Opción A: Telegram (Recomendado — 100% Gratis e Instantáneo)
1. Abre Telegram, busca **`@BotFather`** y envíale el comando `/newbot`.
2. Ponle un nombre (ej. *Panini Restock Monitor*) y un usuario terminado en `bot` (ej. `panini_marvel_dc_bot`).
3. Copia el **Token HTTP API** que te entrega (ej. `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).
4. **¡Importante!** Abre una conversación con tu nuevo bot y presiona **Iniciar (`/start`)** para autorizar que te envíe mensajes.
5. Busca **`@userinfobot`** en Telegram, presiona **Iniciar** y copia tu **`Id`** numérico (ej. `987654321`).

### Opción B: App `ntfy.sh` (El más rápido — Sin registro ni tokens)
1. Descarga la app gratuita **ntfy** en Android / iOS (o abre `https://ntfy.sh` en el navegador).
2. Suscríbete a un tema con un nombre único y secreto, por ejemplo: `panini_restock_mx_8472`.
3. Usa ese mismo nombre en la variable `NTFY_TOPIC=panini_restock_mx_8472`.

### Opción C: Discord Webhook
1. En tu servidor de Discord, ve a **Editar Canal → Integraciones → Webhooks → Nuevo Webhook**.
2. Copia la URL del Webhook y colócala en `DISCORD_WEBHOOK_URL`.

---

## 💻 Paso 2: Probar en tu Computadora (De 0 a Primera Ejecución)

No necesitas instalar paquetes con `pip`. Solo necesitas tener `python3` instalado:

```bash
cd /Users/widevolumes/.gemini/antigravity/scratch/panini-monitor

# 1. Crea tu archivo .env a partir de la plantilla
cp .env.example .env
# Edita .env con tu TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID o NTFY_TOPIC

# 2. Envía una alerta de prueba a tu celular para verificar tu configuración
python3 monitor.py --test-notification

# 3. Ejecuta el primer escaneo (indexa los ~1,511 productos de Marvel y DC)
python3 monitor.py --once
```

---

## ☁️ Paso 3: Despliegue 24/7 en Hosts Gratuitos (3 Métodos)

### Método 1: GitHub Actions (100% Gratis, Sin Tarjeta, Sin Que Se Duerma)
Este método usa los servidores de GitHub para escanear la tienda periódicamente y guarda el archivo `data/panini_state.json` en tu propio repositorio para recordar qué cómics estaban agotados.

1. Crea un repositorio nuevo en GitHub (recomendado: **Público** para tener minutos ilimitados en GitHub Actions) y sube estos archivos:
   ```bash
   git init
   git add .
   git commit -m "feat: agente monitor Tienda Panini Marvel & DC"
   git branch -M main
   git remote add origin https://github.com/TU_USUARIO/panini-monitor.git
   git push -u origin main
   ```
2. En tu repositorio de GitHub, ve a **Settings → Secrets and variables → Actions → New repository secret** y agrega:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - *(Opcional)* `DISCORD_WEBHOOK_URL` o `NTFY_TOPIC`
   - *(Opcional)* `WATCHLIST_KEYWORDS` (déjalo vacío para todos los cómics de Marvel y DC, o pon p. ej. `Omnibus,Must Have,Spider-Man,Batman`)
3. Ve a la pestaña **Actions**, selecciona **Monitoreo 24/7 Tienda Panini (Marvel & DC)** y haz clic en **Run workflow** para crear la base de datos inicial `data/panini_state.json`.

---

### Método 2: Render.com o Koyeb (Contenedor Persistente 24/7 Gratis + UptimeRobot)
Los servicios web gratuitos como **Render** o **Koyeb** apagan el contenedor si no recibe visitas HTTP en 15 minutos. Por eso `monitor.py` incluye un **servidor HTTP Keep-Alive ultraligero** en `/status`.

1. Crea una cuenta gratuita en [Render.com](https://render.com) o [Koyeb.com](https://www.koyeb.com) y conecta tu repositorio de GitHub.
2. Selecciona **New Web Service** (Runtime: **Docker**, Plan: **Free**).
3. En **Environment Variables**, añade:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `CHECK_INTERVAL_SECONDS` = `180` (escaneo cada 3 minutos)
4. Una vez desplegado, copia la URL pública que te da Render/Koyeb (ej. `https://panini-comics-monitor.onrender.com/status`).
5. Crea una cuenta gratuita en [UptimeRobot.com](https://uptimerobot.com) o [cron-job.org](https://cron-job.org) y configura un monitor **HTTP(s)** que visite tu URL `/status` **cada 5 minutos**.
   - ¡Esto mantiene tu agente despierto 24/7 sin interrupciones y además puedes abrir `/status` en tu navegador para ver cuántos cómics de Marvel y DC están en stock o agotados en tiempo real!

---

### Método 3: Oracle Cloud Free Tier / VPS / Raspberry Pi (Docker 24/7)
Si tienes una máquina virtual gratuita (como *Oracle Cloud Always Free* o *Google Cloud e2-micro*):

```bash
docker build -t panini-monitor .
docker run -d \
  --name panini-monitor \
  --restart always \
  --env-file .env \
  -v $(pwd)/data:/app/data \
  -p 8080:8080 \
  panini-monitor
```
