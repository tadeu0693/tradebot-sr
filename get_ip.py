import urllib.request
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

try:
    ip = urllib.request.urlopen("https://api.ipify.org").read().decode()
    log.info(f"🌐 IP PÚBLICO DO SERVIDOR RAILWAY: {ip}")
    log.info(f"👉 Adicione este IP na Binance → Gerenciamento de API → Restrição de IP")
except Exception as e:
    log.error(f"Erro ao obter IP: {e}")
