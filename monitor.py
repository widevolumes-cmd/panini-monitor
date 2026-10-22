#!/usr/bin/env python3
"""
Agente Ligero 24/7 de Monitoreo de Restock - Tienda Panini México (Marvel & DC)
===============================================================================
Diseñado con CERO dependencias externas (100% Python Standard Library) para
consumir <15 MB de RAM y sobrevivir en cualquier entorno gratuito en la nube:
- GitHub Actions (Serverless Cron)
- Render / Koyeb / Railway / Fly.io (incluye servidor HTTP Keep-Alive en $PORT)
- Oracle Cloud Free Tier / Raspberry Pi / VPS (Docker o systemd)

Detecta transiciones de estado: OUT_OF_STOCK -> IN_STOCK
en las categorías oficiales de Tienda Panini México:
- Marvel Comics (ID: 1533)
- DC Comics (ID: 516)
"""

import argparse
import datetime
import html
import http.server
import json
import logging
import os
import socketserver
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("panini-monitor")

# Constantes de Tienda Panini México (Magento 2 GraphQL)
BASE_URL = "https://tiendapanini.com.mx"
GRAPHQL_ENDPOINT = f"{BASE_URL}/graphql"
STORE_CODE = "mexico"

# IDs oficiales de categorías en Tienda Panini México
CATEGORY_MARVEL_ID = "1533"
CATEGORY_DC_ID = "516"
TARGET_CATEGORIES = [CATEGORY_MARVEL_ID, CATEGORY_DC_ID]

# Estado global en memoria para el endpoint de salud (/status)
RUNTIME_STATUS: Dict[str, Any] = {
    "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "last_check_at": None,
    "last_check_success": None,
    "total_tracked": 0,
    "in_stock_count": 0,
    "out_of_stock_count": 0,
    "marvel_count": 0,
    "dc_count": 0,
    "restocks_detected_session": 0,
    "last_error": None,
}


def load_env_file(env_path: str = ".env") -> None:
    """Carga variables desde un archivo .env sin requerir python-dotenv."""
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception as exc:
        logger.warning("No se pudo leer .env: %s", exc)


GRAPHQL_QUERY = """
query GetComics($categories: [String!], $pageSize: Int!, $currentPage: Int!) {
  products(
    filter: { category_id: { in: $categories } }
    pageSize: $pageSize
    currentPage: $currentPage
  ) {
    total_count
    page_info {
      current_page
      page_size
      total_pages
    }
    items {
      id
      sku
      name
      url_key
      stock_status
      price_range {
        minimum_price {
          final_price {
            value
            currency
          }
        }
      }
      small_image {
        url
      }
      categories {
        id
        name
      }
    }
  }
}
"""


def fetch_graphql_page(
    categories: List[str],
    page_size: int = 250,
    current_page: int = 1,
    timeout: int = 25,
    retries: int = 3,
) -> Dict[str, Any]:
    """Consulta una página de productos en el endpoint GraphQL de Magento 2."""
    query_compact = " ".join(GRAPHQL_QUERY.split())
    variables = {
        "categories": categories,
        "pageSize": page_size,
        "currentPage": current_page,
    }
    params = urllib.parse.urlencode(
        {
            "___store": STORE_CODE,
            "query": query_compact,
            "variables": json.dumps(variables),
        }
    )
    url = f"{GRAPHQL_ENDPOINT}?{params}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Store": STORE_CODE,
    }

    ctx = ssl.create_default_context()
    last_err: Optional[Exception] = None

    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
                if "errors" in data and not data.get("data"):
                    raise RuntimeError(f"GraphQL errors: {data['errors']}")
                return data
        except Exception as exc:
            last_err = exc
            wait_s = attempt * 3
            logger.warning(
                "Intento %d/%d falló en página %d (%s). Reintentando en %ds...",
                attempt,
                retries,
                current_page,
                exc,
                wait_s,
            )
            time.sleep(wait_s)

    raise RuntimeError(f"Fallo al consultar página {current_page}: {last_err}")


def classify_publisher(categories: List[Dict[str, Any]], name: str) -> str:
    """Determina si el producto pertenece a Marvel Comics o DC Comics."""
    cat_ids = {str(c.get("id", "")) for c in categories if isinstance(c, dict)}
    cat_names = " ".join(str(c.get("name", "")).lower() for c in categories if isinstance(c, dict))

    is_marvel = CATEGORY_MARVEL_ID in cat_ids or "marvel" in cat_names
    is_dc = CATEGORY_DC_ID in cat_ids or "dc comics" in cat_names

    if is_marvel and is_dc:
        return "Marvel / DC"
    if is_marvel:
        return "Marvel Comics"
    if is_dc:
        return "DC Comics"
    if "marvel" in name.lower():
        return "Marvel Comics"
    return "DC Comics"


def fetch_all_marvel_dc_products(page_size: int = 250) -> Dict[str, Dict[str, Any]]:
    """Descarga el catálogo completo de Marvel Comics y DC Comics desde Tienda Panini."""
    products_by_sku: Dict[str, Dict[str, Any]] = {}
    current_page = 1
    total_pages = 1

    while current_page <= total_pages:
        logger.info("Consultando catálogo Marvel & DC - Página %d/%d...", current_page, total_pages)
        payload = fetch_graphql_page(
            categories=TARGET_CATEGORIES,
            page_size=page_size,
            current_page=current_page,
        )
        products_Node = payload.get("data", {}).get("products", {})
        page_info = products_Node.get("page_info", {})
        total_pages = int(page_info.get("total_pages") or 1)
        items = products_Node.get("items") or []

        for item in items:
            sku = str(item.get("sku") or item.get("id") or "").strip()
            if not sku:
                continue

            url_key = str(item.get("url_key") or "").strip()
            product_url = f"{BASE_URL}/{url_key}" if url_key else BASE_URL

            price_info = (
                item.get("price_range", {})
                .get("minimum_price", {})
                .get("final_price", {})
            )
            price_val = price_info.get("value", 0.0)
            currency = price_info.get("currency", "MXN")

            image_url = (item.get("small_image") or {}).get("url") or ""
            categories = item.get("categories") or []
            name = str(item.get("name") or sku).strip()
            publisher = classify_publisher(categories, name)
            stock_status = str(item.get("stock_status") or "OUT_OF_STOCK").upper()

            products_by_sku[sku] = {
                "id": item.get("id"),
                "sku": sku,
                "name": name,
                "publisher": publisher,
                "stock_status": stock_status,
                "price": price_val,
                "currency": currency,
                "url": product_url,
                "image_url": image_url,
            }

        current_page += 1
        if current_page <= total_pages:
            time.sleep(0.5)  # Pausa cortés para no saturar el servidor

    return products_by_sku


def load_state(state_file: str) -> Dict[str, Dict[str, Any]]:
    """Carga el estado previo de productos desde un archivo JSON ligero."""
    if not os.path.exists(state_file):
        return {}
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict) and "products" in data:
                return data["products"]
            if isinstance(data, dict):
                return data
    except Exception as exc:
        logger.warning("No se pudo cargar %s (%s). Se iniciará estado limpio.", state_file, exc)
    return {}


def save_state(state_file: str, products: Dict[str, Dict[str, Any]]) -> None:
    """Guarda el estado compacto en disco (solo campos necesarios para ahorrar espacio)."""
    os.makedirs(os.path.dirname(os.path.abspath(state_file)), exist_ok=True)
    compact_products = {
        sku: {
            "name": p["name"],
            "publisher": p["publisher"],
            "stock_status": p["stock_status"],
            "price": p["price"],
            "url": p["url"],
            "updated_at": p.get("updated_at"),
        }
        for sku, p in products.items()
    }
    payload = {
        "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "total_products": len(compact_products),
        "products": compact_products,
    }
    tmp_file = f"{state_file}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp_file, state_file)


def matches_watchlist(product: Dict[str, Any], watchlist_raw: str) -> bool:
    """
    Si WATCHLIST_KEYWORDS está vacío, monitorea TODOS los cómics de Marvel y DC.
    Si tiene palabras clave separadas por coma, filtra por nombre, SKU o URL.
    """
    if not watchlist_raw or not watchlist_raw.strip():
        return True
    keywords = [k.strip().lower() for k in watchlist_raw.split(",") if k.strip()]
    if not keywords:
        return True
    haystack = f"{product['name']} {product['sku']} {product['url']}".lower()
    return any(kw in haystack for kw in keywords)


# ==============================================================================
# CANALES DE NOTIFICACIÓN (Telegram, Discord, ntfy.sh)
# ==============================================================================

def send_telegram_alert(product: Dict[str, Any], token: str, chat_id: str, reason: str = "RESTOCK") -> bool:
    """Envía alerta rica a Telegram."""
    emoji = "🔴" if "Marvel" in product["publisher"] else "🔵"
    badge = "🔥 ¡DE VUELTA EN STOCK!" if reason == "RESTOCK" else "🆕 ¡NUEVO EN STOCK!"
    caption = (
        f"<b>{badge}</b>\n"
        f"{emoji} <b>Editorial:</b> {html.escape(product['publisher'])}\n"
        f"📖 <b>Título:</b> {html.escape(product['name'])}\n"
        f"💰 <b>Precio:</b> ${product['price']:,.2f} {html.escape(product['currency'])}\n"
        f"🏷️ <b>SKU:</b> <code>{html.escape(product['sku'])}</code>\n\n"
        f"🛒 <a href=\"{html.escape(product['url'])}\">Comprar ahora en Tienda Panini</a>"
    )

    ctx = ssl.create_default_context()
    try:
        api_url = f"https://api.telegram.org/bot{token}/sendMessage"
        body = json.dumps(
            {
                "chat_id": chat_id,
                "text": caption,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            api_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.error("Error enviando alerta a Telegram para %s: %s", product["sku"], exc)
        return False


def send_discord_alert(product: Dict[str, Any], webhook_url: str, reason: str = "RESTOCK") -> bool:
    """Envía alerta con Embed enriquecido a un Webhook de Discord."""
    color = 0xED1D24 if "Marvel" in product["publisher"] else 0x0476F2
    title_prefix = "🔥 ¡RESTOCK DISPONIBLE!" if reason == "RESTOCK" else "🆕 ¡NUEVO LANZAMIENTO!"
    embed: Dict[str, Any] = {
        "title": f"{title_prefix} — {product['name']}",
        "url": product["url"],
        "color": color,
        "fields": [
            {"name": "Editorial", "value": product["publisher"], "inline": True},
            {"name": "Precio", "value": f"${product['price']:,.2f} {product['currency']}", "inline": True},
            {"name": "SKU", "value": f"`{product['sku']}`", "inline": True},
        ],
        "footer": {"text": "Agente Monitor Tienda Panini 24/7"},
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    if product.get("image_url"):
        embed["thumbnail"] = {"url": product["image_url"]}

    payload = json.dumps({"embeds": [embed]}).encode("utf-8")
    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "PaniniMonitorBot/1.0",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        logger.error("Error enviando alerta a Discord para %s: %s", product["sku"], exc)
        return False


def send_ntfy_alert(product: Dict[str, Any], topic_url: str, reason: str = "RESTOCK") -> bool:
    """Envía alerta push instantánea vía ntfy.sh con prioridad máxima (5) para forzar banner push."""
    raw_topic = topic_url.strip()
    if raw_topic.startswith("http://") or raw_topic.startswith("https://"):
        parsed = urllib.parse.urlparse(raw_topic)
        endpoint = f"{parsed.scheme}://{parsed.netloc}"
        topic = parsed.path.strip("/")
    else:
        endpoint = "https://ntfy.sh"
        topic = raw_topic.strip("/")

    title = f"¡Restock {product['publisher']}! {product['name'][:45]}"
    message = (
        f"Ya disponible en Tienda Panini\n"
        f"Precio: ${product['price']:,.2f} {product['currency']} | SKU: {product['sku']}\n"
        f"{product['url']}"
    )
    payload: Dict[str, Any] = {
        "topic": topic,
        "title": title,
        "message": message,
        "priority": 5,  # Prioridad 5 (max/urgent) despierta APNs/FCM de inmediato con sonido y banner
        "tags": ["rotating_light", "books"],
        "click": product["url"],
        "actions": [
            {
                "action": "view",
                "label": "Comprar en Panini",
                "url": product["url"],
            }
        ],
    }
    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        logger.error("Error enviando alerta a ntfy para %s: %s", product["sku"], exc)
        return False


def notify_all_channels(product: Dict[str, Any], reason: str = "RESTOCK") -> None:
    """Envía la notificación por todos los canales configurados en variables de entorno."""
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    discord_webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    ntfy_topic = os.environ.get("NTFY_TOPIC", "").strip()

    logger.info(
        "🔔 ALERTA [%s] (%s): %s - $%s MXN -> %s",
        reason,
        product["publisher"],
        product["name"],
        product["price"],
        product["url"],
    )

    sent_any = False
    if tg_token and tg_chat_id:
        if send_telegram_alert(product, tg_token, tg_chat_id, reason=reason):
            sent_any = True
    if discord_webhook:
        if send_discord_alert(product, discord_webhook, reason=reason):
            sent_any = True
    if ntfy_topic:
        if send_ntfy_alert(product, ntfy_topic, reason=reason):
            sent_any = True

    if not sent_any:
        logger.info(
            "💡 (Modo consola: configura TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID, "
            "DISCORD_WEBHOOK_URL o NTFY_TOPIC para recibir alertas en tu celular)."
        )


# ==============================================================================
# LÓGICA PRINCIPAL DE DETECCIÓN DE CAMBIOS DE STOCK
# ==============================================================================

def run_check_cycle(state_file: str) -> Tuple[int, int]:
    """
    Ejecuta un ciclo completo de verificación:
    1. Carga el estado anterior.
    2. Descarga el catálogo actual de Marvel y DC.
    3. Compara qué productos pasaron de OUT_OF_STOCK a IN_STOCK.
    4. Envía notificaciones y actualiza el archivo de estado.
    Retorna (total_productos, total_restocks_detectados).
    """
    prev_state = load_state(state_file)
    is_first_run = len(prev_state) == 0

    current_products = fetch_all_marvel_dc_products()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    watchlist_raw = os.environ.get("WATCHLIST_KEYWORDS", "")
    alert_on_new = os.environ.get("ALERT_ON_NEW_PRODUCTS", "false").lower() in ("1", "true", "yes")

    restocks_found = 0
    in_stock_total = 0
    out_of_stock_total = 0
    marvel_total = 0
    dc_total = 0

    for sku, prod in current_products.items():
        if prod["stock_status"] == "IN_STOCK":
            in_stock_total += 1
        else:
            out_of_stock_total += 1

        if "Marvel" in prod["publisher"]:
            marvel_total += 1
        if "DC" in prod["publisher"]:
            dc_total += 1

        old_entry = prev_state.get(sku)
        old_status = old_entry.get("stock_status") if old_entry else None

        if old_status != prod["stock_status"]:
            prod["updated_at"] = now_iso
        else:
            prod["updated_at"] = (old_entry or {}).get("updated_at", now_iso)

        # Caso principal solicitado: Estaba AGOTADO (OUT_OF_STOCK) y volvió a estar DISPONIBLE (IN_STOCK)
        if not is_first_run:
            if old_status == "OUT_OF_STOCK" and prod["stock_status"] == "IN_STOCK":
                if matches_watchlist(prod, watchlist_raw):
                    notify_all_channels(prod, reason="RESTOCK")
                    restocks_found += 1
            elif old_status is None and prod["stock_status"] == "IN_STOCK" and alert_on_new:
                if matches_watchlist(prod, watchlist_raw):
                    notify_all_channels(prod, reason="NEW")
                    restocks_found += 1

    # Preservar productos que temporalmente no hayan salido en la consulta para no perder su historial OUT_OF_STOCK
    for old_sku, old_prod in prev_state.items():
        if old_sku not in current_products:
            current_products[old_sku] = old_prod

    save_state(state_file, current_products)

    # Actualizar métricas en memoria
    RUNTIME_STATUS["last_check_at"] = now_iso
    RUNTIME_STATUS["last_check_success"] = True
    RUNTIME_STATUS["total_tracked"] = len(current_products)
    RUNTIME_STATUS["in_stock_count"] = in_stock_total
    RUNTIME_STATUS["out_of_stock_count"] = out_of_stock_total
    RUNTIME_STATUS["marvel_count"] = marvel_total
    RUNTIME_STATUS["dc_count"] = dc_total
    RUNTIME_STATUS["restocks_detected_session"] += restocks_found
    RUNTIME_STATUS["last_error"] = None

    if is_first_run:
        logger.info(
            "✅ Inventario inicial indexado con éxito: %d productos de Marvel y DC "
            "(%d disponibles, %d agotados en vigilancia para restock).",
            len(current_products),
            in_stock_total,
            out_of_stock_total,
        )
    else:
        logger.info(
            "✔ Ciclo completado: %d productos monitoreados (%d en stock, %d agotados). "
            "Restocks detectados en este ciclo: %d.",
            len(current_products),
            in_stock_total,
            out_of_stock_total,
            restocks_found,
        )

    return len(current_products), restocks_found


# ==============================================================================
# SERVIDOR HTTP LIGERO PARA KEEP-ALIVE EN HOSTS GRATUITOS (Render, Koyeb, Fly.io)
# ==============================================================================

class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/", "/health", "/status"):
            body = json.dumps(RUNTIME_STATUS, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        # Silenciar logs de cada ping de UptimeRobot para no ensuciar la consola
        return


def start_health_server(port: int) -> None:
    """Inicia un servidor HTTP en segundo plano para mantener vivo el contenedor gratuito."""
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("0.0.0.0", port), HealthCheckHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("🌐 Servidor Keep-Alive HTTP activo en el puerto %d (GET /status)", port)


def main() -> None:
    load_env_file()

    parser = argparse.ArgumentParser(
        description="Agente 24/7 de Monitoreo de Restock en Tienda Panini México (Marvel & DC)"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Ejecuta un solo escaneo y termina (ideal para GitHub Actions / Cron).",
    )
    parser.add_argument(
        "--test-notification",
        action="store_true",
        help="Envía una notificación de prueba a tus canales configurados y termina.",
    )
    parser.add_argument(
        "--list-oos",
        action="store_true",
        help="Muestra en pantalla todos los cómics de Marvel y DC actualmente agotados en vigilancia.",
    )
    parser.add_argument(
        "--state-file",
        default=os.environ.get("STATE_FILE", "data/panini_state.json"),
        help="Ruta del archivo JSON donde se persiste el estado del inventario.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("CHECK_INTERVAL_SECONDS", "300")),
        help="Segundos entre cada escaneo en modo continuo 24/7 (default: 300 = 5 min).",
    )
    args = parser.parse_args()

    if args.list_oos:
        state = load_state(args.state_file)
        oos = [(sku, p) for sku, p in state.items() if p.get("stock_status") == "OUT_OF_STOCK"]
        print(f"\n📋 CÓMICS AGOTADOS EN VIGILANCIA DE RESTOCK ({len(oos)} encontrados):\n" + "=" * 75)
        for sku, p in sorted(oos, key=lambda x: (x[1].get("publisher", ""), x[1].get("name", ""))):
            print(f"• [{p.get('publisher')}] {p.get('name')} — ${p.get('price')} MXN ({sku})\n  🔗 {p.get('url')}")
        return

    if args.test_notification:
        dummy_product = {
            "id": 999999,
            "sku": "QMMUS002-TEST",
            "name": "[PRUEBA] Daredevil: El Hombre Sin Miedo (Marvel Must Have)",
            "publisher": "Marvel Comics",
            "stock_status": "IN_STOCK",
            "price": 389.0,
            "currency": "MXN",
            "url": "https://tiendapanini.com.mx/daredevil-el-hombre-sin-miedo-marvel-must-have",
            "image_url": "https://tiendapanini.com.mx/media/logo/stores/5/panini-logo_2_2_1.png",
        }
        notify_all_channels(dummy_product, reason="RESTOCK")
        return

    if args.once:
        run_check_cycle(args.state_file)
        return

    # Modo Demonio 24/7: iniciar servidor HTTP si estamos en la nube o si PORT está definido
    port = int(os.environ.get("PORT", "8080"))
    start_health_server(port)

    logger.info(
        "🚀 Iniciando monitoreo continuo 24/7 cada %d segundos. Archivo de estado: %s",
        args.interval,
        args.state_file,
    )
    while True:
        try:
            run_check_cycle(args.state_file)
        except Exception as exc:
            RUNTIME_STATUS["last_check_success"] = False
            RUNTIME_STATUS["last_error"] = str(exc)
            logger.error("❌ Error durante el ciclo de monitoreo: %s", exc)
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    main()
