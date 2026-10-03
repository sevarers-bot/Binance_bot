import os
import sys
import time
import datetime
from datetime import timezone, timedelta
import requests
from flask import Flask
from threading import Thread

sys.stdout.reconfigure(line_buffering=True)

# ==================== WEB SUNUCUSU ====================
app = Flask('')

@app.route('/')
def home():
    return "Bot Aktif!"

def run_web_server():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_web_server)
    t.daemon = True
    t.start()

# ==================== KULLANICI AYARLARI ====================
TELEGRAM_TOKEN = "8951230002:AAFPbwIJ1Ky-oKVg1b4rhSQ7W9LsTnrHJDs"  # Telegram Bot Token
CHAT_ID = "6593284503"          # Telegram Chat ID

# MANUEL HACİM VE ZAMAN DİLİMİ AYARLARI
VOLUME_INTERVAL = "5m"        # Hacim patlaması kontrolü yapılan zaman dilimi ("5m", "15m", "1h")
VOLUME_MULTIPLIER = 3.0       # Hacim katı şartı (4.0 = 4 Katı)
LOOKBACK_PERIOD = 20          # Ortalaması alınacak geçmiş mum sayısı

# KATI HACİM FİLTRELERİ (ÖNEMSİZ / ÇÖP COINLERI ELER)
MIN_CANDLE_VOL_USDT = 300_000   # Mevcut mum hacmi EN AZ 300.000$(0.3M$) olmalı
MIN_AVG_VOL_USDT = 50_000       # Ortalama mum hacmi EN AZ 50.000$ olmalı
MIN_24H_VOLUME_USDT = 5_000_000 # 24s genel hacmi en az 5M$ olmalı (Tüm aktif spot coinleri kapsar)

# BINANCE SPOT API URL
BINANCE_SPOT_URL = "https://api.binance.com"

# Gelişmiş ve Genişletilmiş Kategori Haritası
CATEGORY_MAP = {
    # BNB & BUSD / Stablecoinler
    "BNB": "BNB Chain / Exchange Token", "USDC": "Stablecoin", "USDT": "Stablecoin", "FDUSD": "Stablecoin",
    
    # Layer 1 / Mainnet
    "BTC": "Layer 1 / Store of Value", "ETH": "Layer 1", "SOL": "Layer 1", "ADA": "Layer 1",
    "AVAX": "Layer 1", "NEAR": "Layer 1 / AI", "SUI": "Layer 1", "APT": "Layer 1", "SEI": "Layer 1",
    "DOT": "Layer 1", "ATOM": "Layer 1", "FTM": "Layer 1", "INJ": "Layer 1", "ALGO": "Layer 1",
    "XRP": "Layer 1 / Payment", "LTC": "Layer 1 / Payment", "TRX": "Layer 1", "TON": "Layer 1",
    
    # Layer 2 / Scaling
    "MATIC": "Layer 2", "POL": "Layer 2", "OP": "Layer 2", "ARB": "Layer 2", "MANTA": "Layer 2",
    "STRK": "Layer 2", "ZK": "Layer 2", "METIS": "Layer 2", "BLAST": "Layer 2",
    
    # AI / Yapay Zeka
    "FET": "AI (Yapay Zeka)", "AGIX": "AI (Yapay Zeka)", "OCEAN": "AI (Yapay Zeka)", "RENDER": "AI / DePIN",
    "TAO": "AI (Yapay Zeka)", "ARKM": "AI / Analytics", "WLD": "AI / Identity", "GRT": "AI / Indexing",
    
    # DeFi / DEX / Lending
    "UNI": "DeFi / DEX", "AAVE": "DeFi / Lending", "MKR": "DeFi", "CRV": "DeFi", "LDO": "DeFi / Staking",
    "PENDLE": "DeFi / Yield", "ENA": "DeFi / Synthetic Dollar", "RAY": "DeFi / DEX", "CAKE": "DeFi / DEX",
    
    # Meme Coins
    "DOGE": "Meme", "SHIB": "Meme", "PEPE": "Meme", "BONK": "Meme", "FLOKI": "Meme", "WIF": "Meme",
    "BOME": "Meme", "MEME": "Meme", "POPCAT": "Meme", "1000SATS": "Meme / Ordinals",
    
    # Infrastructure / RWA / Oracle
    "LINK": "Oracle / RWA", "PYTH": "Oracle", "TIA": "Modular Blockchain", "ALT": "Modular / Restaking",
    "ONDO": "RWA (Real World)", "RNDR": "DePIN / GPU", "FIL": "Storage / DePIN"
}

def get_coin_category(symbol):
    """Coin'in kategorisini tespit eder, yoksa Binance API'den etiket sorgular"""
    base_asset = symbol.replace("USDT", "").replace("1000", "").replace("USDC", "")
    
    # 1. Tanımlı Haritadan Bak
    if base_asset in CATEGORY_MAP:
        return CATEGORY_MAP[base_asset]
    
    # 2. Haritada yoksa varsayılan akıllı sınıflandırma
    if "DOWN" in symbol or "UP" in symbol:
        return "Leveraged Token"
    
    return "Diğer / Altcoin"


# ==================== YARDIMCI FONKSİYONLAR ====================

def get_tr_time():
    """Türkiye Saatini (GMT+3) Döndürür"""
    return (datetime.datetime.now(timezone.utc) + timedelta(hours=3)).strftime('%H:%M:%S')

def send_telegram_msg(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Telegram Hatası: {e}", flush=True)

def get_coin_category(symbol):
    """Coin'in grubunu/kategorisini tespit eder"""
    base_asset = symbol.replace("USDT", "").replace("1000", "")
    return CATEGORY_MAP.get(base_asset, "Spot Altcoin")

def get_all_spot_usdt_pairs():
    """Filtreye uygun tüm SPOT USDT çiftlerini getirir"""
    try:
        url = f"{BINANCE_SPOT_URL}/api/v3/ticker/24hr"
        res = requests.get(url, timeout=6).json()
        if not isinstance(res, list):
            return []

        valid_pairs = []
        for coin in res:
            if isinstance(coin, dict) and 'symbol' in coin and 'quoteVolume' in coin:
                symbol = coin['symbol']
                try:
                    vol_24h = float(coin['quoteVolume'])
                except (ValueError, TypeError):
                    continue

                # Sadece doğrudan USDT ile biten normal Spot çiftler (UP/DOWN/BEAR/BULL tokenlar hariç)
                if symbol.endswith("USDT") and not any(x in symbol for x in ["UPUSDT", "DOWNUSDT", "BEARUSDT", "BULLUSDT"]):
                    if vol_24h >= MIN_24H_VOLUME_USDT:
                        valid_pairs.append({
                            "symbol": symbol,
                            "price": float(coin.get('lastPrice', 0))
                        })
        return valid_pairs
    except Exception as e:
        print(f"Spot Sembol Çekme Hatası: {e}", flush=True)
        return []

def check_bullish_volume_spike(symbol, interval, multiplier, lookback):
    """
    Sadece YUKARI YÖNLÜ (Yeşil mum) hacim patlamalarını kontrol eder.
    """
    try:
        url = f"{BINANCE_SPOT_URL}/api/v3/klines"
        params = {"symbol": symbol, "interval": interval, "limit": lookback + 1}
        res = requests.get(url, params=params, timeout=4).json()
        
        if isinstance(res, list) and len(res) >= lookback:
            volumes = [float(k[7]) for k in res] # Index 7 = Toplam USDT Hacmi
            
            current_kline = res[-1]
            open_price = float(current_kline[1])
            close_price = float(current_kline[4])
            current_volume = volumes[-1]  # Şu anki mumun toplam USDT hacmi
            
            # --- SADECE YÜKSELEN (YEŞİL) MUM ŞARTI ---
            if close_price <= open_price:
                return False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
            
            # Index 10 = Piyasa emriyle yapılan GERÇEK Alış (Taker Buy) USDT Hacmi
            taker_buy_volume = float(current_kline[10]) 
            taker_sell_volume = current_volume - taker_buy_volume
            
            past_volumes = volumes[:-1]   # Geçmiş N mum hacmi
            avg_volume = sum(past_volumes) / len(past_volumes)
            
            # Minimum hacim kontrolleri
            if current_volume < MIN_CANDLE_VOL_USDT or avg_volume < MIN_AVG_VOL_USDT:
                return False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
            
            if avg_volume > 0:
                ratio = current_volume / avg_volume
                if ratio >= multiplier:
                    price_change_pct = ((close_price - open_price) / open_price) * 100
                    return True, current_volume, avg_volume, ratio, taker_buy_volume, taker_sell_volume, price_change_pct
                    
        return False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    except Exception:
        return False, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0

# ==================== TARAMA SÜRECİ ====================

def run_scanner():
    now_str = get_tr_time()
    print(f"[{now_str}] Spot Yükseliş Hacmi Taraması ({VOLUME_INTERVAL} - {VOLUME_MULTIPLIER} Kat) Başlatıldı...", flush=True)
    
    pairs = get_all_spot_usdt_pairs()
    print(f"[{now_str}] Taranacak Spot Çift Sayısı: {len(pairs)}", flush=True)

    if not pairs:
        return

    match_count = 0

    for coin in pairs:
        symbol = coin['symbol']
        price = coin['price']

        time.sleep(0.08)  # API rate limit koruması
        
        is_spike, current_vol, avg_vol, ratio, long_vol, short_vol, price_change = check_bullish_volume_spike(
            symbol, 
            VOLUME_INTERVAL, 
            VOLUME_MULTIPLIER, 
            LOOKBACK_PERIOD
        )

        if is_spike:
            category = get_coin_category(symbol)
            
            current_vol_m = current_vol / 1_000_000
            avg_vol_m = avg_vol / 1_000_000
            long_vol_m = long_vol / 1_000_000
            short_vol_m = short_vol / 1_000_000
            match_count += 1

            msg = (
                f"🚀 *SPOT YUKARI YÖNLÜ HACİM PATLAMASI ({VOLUME_INTERVAL})*\n\n"
                f"🪙 *Sembol:* #{symbol}\n"
                f"🏷️ *Kategori:* `{category}`\n"
                f"💵 *Fiyat:* `{price}` *(+%{price_change:.2f})*\n\n"
                f"⚡ *Hacim Artışı:* `{ratio:.2f} Kat` ({VOLUME_MULTIPLIER}x Üzeri)\n"
                f"📊 *Mevcut ({VOLUME_INTERVAL}) Toplam Hacim:* `${current_vol_m:.2f}M`\n"
                f"🟢 *Alış (Long) Hacmi:* `${long_vol_m:.2f}M`\n"
                f"🔴 *Satış (Short) Hacmi:* `${short_vol_m:.2f}M`\n"
                f"📈 *Ortalama Hacim:* `${avg_vol_m:.2f}M`\n\n"
                f"🔗 [Binance Spot](https://www.binance.com/en/trade/{symbol})"
            )
            send_telegram_msg(msg)
            print(f"-> SPOT SİNYAL: {symbol} [{category}] (+%{price_change:.2f}) - {ratio:.1f}x Hacim", flush=True)

    print(f"[{now_str}] Tarama Bitti. Bulunan Spot Yükseliş Sinyali Sayısı: {match_count}", flush=True)

# ==================== ANA DÖNGÜ ====================

if __name__ == "__main__":
    keep_alive()
    time.sleep(2)
    send_telegram_msg(f"🟢 *Spot Yükseliş Hacim Tarayıcısı Aktif!*\nZaman Dilimi: `{VOLUME_INTERVAL}` | Eşik: `{VOLUME_MULTIPLIER}x`\nFiltre: *Sadece Yeşil Mum & Spot Piyasa*")
    
    while True:
        try:
            run_scanner()
        except Exception as e:
            print(f"Döngü Hatası: {e}", flush=True)
        
        time.sleep(300)  # 5 dakikada bir tarar
