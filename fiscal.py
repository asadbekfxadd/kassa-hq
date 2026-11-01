"""
Фискализация (Узбекистан, ОФД / soliq.uz) через FiscalDriveService (ФМ версии 0400).

FiscalDriveService ставится на тот же ПК, что и касса, и слушает http://127.0.0.1:3449
(swagger: http://127.0.0.1:3449/swagger/index.html).

Режимы:
  off      — без фискализации (только учёт, печать нефискального чека)
  mock     — встроенный демо-ФМ: всё работает без железа, ФП/QR фиктивные
  service  — реальный FiscalDriveService (боевой ФМ или официальный эмулятор)

Все суммы в ФМ передаются в тийинах (1 сум = 100 тийин), количество — в тысячных (1 шт = 1000).
"""
import hashlib
import json
import random
import threading
import time
from datetime import datetime

import requests


class FiscalError(Exception):
    pass


def _now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- receipt builder
def build_receipt_json(sale, items, settings, refund_of=None):
    """
    sale:  dict (operation, cash, card, payment_type)
    items: list of dicts (name, barcode, qty, price, discount, vat_percent, spic, package_code,
                          units_code, owner_type)
    Возвращает JSON-чек по формату FiscalDriveService.
    """
    f_items = []
    total_net = 0
    for it in items:
        price_t = int(round(float(it["qty"]) * float(it["price"]) * 100))      # сумма позиции без скидки
        disc_t = int(round(float(it.get("discount") or 0) * 100))
        if disc_t > price_t:
            disc_t = price_t
        net = price_t - disc_t
        vat_pct = int(it.get("vat_percent") or 0)
        vat = int(round(net * vat_pct / (100 + vat_pct))) if vat_pct else 0
        total_net += net
        row = {
            "Name": (it["name"] or "")[:63],
            "Barcode": it.get("barcode") or "",
            "Labels": [],
            "SPIC": it.get("spic") or "",
            "Units": int(it.get("units_code") or 0),
            "PackageCode": it.get("package_code") or "",
            "OwnerType": int(it.get("owner_type") or 0),
            "Price": price_t,
            "VATPercent": vat_pct,
            "VAT": vat,
            "Amount": int(round(float(it["qty"]) * 1000)),
            "Discount": disc_t,
            "Other": 0,
        }
        if it.get("marking"):
            row["Labels"] = [m for m in str(it["marking"]).split(",") if m.strip()]
        f_items.append(row)

    debt_t = int(round(float(sale.get("debt") or 0) * 100))   # часть в насию — деньги не получены
    paid_t = max(total_net - debt_t, 0)
    qr_t = int(round(float(sale.get("qr") or 0) * 100))
    card_t = int(round(float(sale.get("card") or 0) * 100)) + qr_t   # QR (Payme/Click) — безналичная оплата
    if card_t > paid_t:
        card_t = paid_t
    cash_t = paid_t - card_t  # чистая наличная выручка (без сдачи)

    if qr_t and qr_t == card_t and not cash_t:
        ptype = 3                                           # оплата по QR-коду
    elif card_t and cash_t:
        ptype = 4
    elif card_t:
        ptype = 2
    elif cash_t:
        ptype = 1
    else:
        ptype = 4 if debt_t else 1

    rec = {
        "Time": _now_str(),
        "ReceivedCash": cash_t,
        "ReceivedCard": card_t,
        "Type": 2 if float(sale.get("debt") or 0) > 0 else 0,   # 2 = кредит (насия)
        "Operation": 1 if sale.get("operation") == "refund" else 0,
        "PaymentType": ptype,
        "Items": f_items,
    }
    try:
        lat = float(settings.get("geo_lat") or 0)
        lng = float(settings.get("geo_lng") or 0)
        if lat and lng:
            rec["Location"] = {"Latitude": lat, "Longitude": lng}
    except ValueError:
        pass

    phone = (sale.get("customer_phone") or "").strip()
    if phone:
        rec["ExtraInfo"] = {"PhoneNumber": phone}

    if refund_of:
        dt = (refund_of.get("fiscal_datetime") or "").replace("-", "").replace(":", "").replace(" ", "")
        rec["RefundInfo"] = {
            "TerminalID": refund_of["terminal_id"],
            "ReceiptSeq": int(refund_of["receipt_seq"]),
            "DateTime": dt,
            "FiscalSign": refund_of["fiscal_sign"],
        }
    return rec


# ---------------------------------------------------------------- clients
class BaseFiscal:
    mode = "off"

    def list_drives(self): return []
    def info(self): return {}
    def memory_info(self): return {}
    def sync_state(self): return "OK"
    def zreport_open(self): return "OK"
    def zreport_close(self): return "OK"
    def get_txid(self, receipt): raise FiscalError("Фискализация выключена")
    def register_txid(self, txid): raise FiscalError("Фискализация выключена")
    def sync_receipts(self, n=32): return {"Items": [], "SuccessfulsCount": 0}
    def sync_zreports(self, n=32): return {"Items": [], "SuccessfulsCount": 0}
    def pos_lock(self): raise FiscalError("Недоступно в этом режиме")


class MockFiscal(BaseFiscal):
    """Встроенный демо-ФМ. Ведёт себя как настоящий: TXID -> регистрация -> ФП + QR."""
    mode = "mock"
    _lock = threading.Lock()
    _state = {"seq": 0, "txids": {}, "registered": {}, "z_open": False, "unsent": 0}

    def __init__(self, factory_id="DEMO000000000000000000000000000000000"):
        self.fid = factory_id

    def list_drives(self):
        return [{"ReaderName": "DEMO", "FactoryID": self.fid, "AppletVersion": "0400"}]

    def info(self):
        return {"TerminalID": "ZZ000000000000", "Locked": False, "POSLocked": False, "AppletVersion": "0400"}

    def memory_info(self):
        s = self._state
        return {"TerminalID": "ZZ000000000000", "ReceiptSeq": s["seq"], "ReceiptsCount": s["unsent"],
                "LastOperationTime": _now_str(), "FirstUnacknowledgedReceiptTime": "", "ZReportsCount": 0}

    def zreport_open(self):
        self._state["z_open"] = True
        return "OK"

    def zreport_close(self):
        self._state["z_open"] = False
        return "OK"

    def get_txid(self, receipt):
        with self._lock:
            txid = str(int(time.time() * 1000)) + str(random.randint(100, 999))
            self._state["txids"][txid] = receipt
            return txid

    def register_txid(self, txid):
        with self._lock:
            if txid in self._state["registered"]:
                return self._state["registered"][txid]
            if txid not in self._state["txids"]:
                raise FiscalError("TXID не найден")
            self._state["seq"] += 1
            self._state["unsent"] += 1
            r = self._state["txids"][txid]
            dt = r["Time"]
            compact = dt.replace("-", "").replace(":", "").replace(" ", "")
            fs = str(random.randint(10 ** 11, 10 ** 12 - 1))
            res = {
                "TerminalID": "ZZ000000000000",
                "ReceiptSeq": self._state["seq"],
                "DateTime": dt,
                "FiscalSign": fs,
                "QRCodeURL": f"https://ofd.soliq.uz/check?t=ZZ000000000000&r={self._state['seq']}&c={compact}&s={fs}",
            }
            self._state["registered"][txid] = res
            return res

    def sync_receipts(self, n=32):
        c = self._state["unsent"]
        self._state["unsent"] = 0
        return {"Items": [], "SuccessfulsCount": c}


class ServiceFiscal(BaseFiscal):
    """Клиент реального FiscalDriveService (REST)."""
    mode = "service"

    def __init__(self, base_url, factory_id, pos_secret="", timeout=25):
        self.base = (base_url or "http://127.0.0.1:3449").rstrip("/")
        self.fid = (factory_id or "").strip()
        self.secret = (pos_secret or "").strip().lower()
        self.timeout = timeout

    # --- transport ---------------------------------------------------------
    def _call(self, path, method="GET", params=None, body=None, headers=None, raw_body=False):
        url = self.base + path
        h = {"Accept": "application/json"}
        if headers:
            h.update(headers)
        try:
            if method == "GET":
                r = requests.get(url, params=params, headers=h, timeout=self.timeout)
            else:
                if raw_body:
                    h["Content-Type"] = "application/json"
                    r = requests.post(url, params=params, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                      headers=h, timeout=self.timeout)
                else:
                    r = requests.post(url, params=params, json=body, headers=h, timeout=self.timeout)
                if r.status_code == 405:  # на случай, если метод в swagger объявлен как GET
                    r = requests.get(url, params=params, headers=h, timeout=self.timeout)
        except requests.ConnectionError:
            raise FiscalError("Нет связи с FiscalDriveService (" + self.base + "). Проверьте, что служба запущена.")
        except requests.Timeout:
            raise FiscalError("FiscalDriveService не ответил вовремя")
        txt = r.text.strip()
        if r.status_code >= 400:
            raise FiscalError(f"ФМ ошибка {r.status_code}: {txt[:400]}")
        try:
            return r.json()
        except ValueError:
            return txt.strip('"')

    def _need_fid(self):
        if not self.fid:
            raise FiscalError("Не указан заводской номер ФМ (FactoryID) в настройках")
        return self.fid

    def _pos_headers(self):
        if not self.secret:
            return {}
        ch = self._call(f"/POS/Challenge/{self._need_fid()}")
        if isinstance(ch, dict):
            ch = ch.get("Challenge") or ch.get("challenge") or ""
        auth = hashlib.sha256(bytes.fromhex(self.secret) + bytes.fromhex(str(ch))).hexdigest()
        return {"X-POS-Auth": auth}

    # --- API ---------------------------------------------------------------
    def list_drives(self):
        return self._call("/FiscalDrive/List")

    def info(self):
        return self._call(f"/FiscalDrive/Info/{self._need_fid()}")

    def memory_info(self):
        return self._call(f"/FiscalDrive/FiscalMemory/Info/{self._need_fid()}")

    def sync_state(self):
        return self._call(f"/FiscalDrive/State/Sync/{self._need_fid()}", "POST")

    def zreport_open(self):
        return self._call(f"/FiscalDrive/ZReport/Open/{self._need_fid()}", "POST", headers=self._pos_headers())

    def zreport_close(self):
        return self._call(f"/FiscalDrive/ZReport/Close/{self._need_fid()}", "POST", headers=self._pos_headers())

    def get_txid(self, receipt):
        res = self._call(f"/FiscalDrive/Receipt/GetTXID/{self._need_fid()}", "POST", body=receipt, raw_body=True)
        if isinstance(res, dict):
            res = res.get("TXID") or res.get("txid") or res.get("TxID")
        if not res:
            raise FiscalError("ФМ не вернул TXID")
        return str(res)

    def register_txid(self, txid):
        res = self._call(f"/FiscalDrive/Receipt/RegisterTXID/{self._need_fid()}", "POST",
                         params={"TXID": txid}, body={"TXID": txid}, headers=self._pos_headers())
        if not isinstance(res, dict) or "FiscalSign" not in res:
            raise FiscalError(f"Неожиданный ответ ФМ: {str(res)[:300]}")
        return res

    def sync_receipts(self, n=32):
        return self._call(f"/DataBase/Files/Sync/FullReceipts/{self._need_fid()}", "POST", params={"ItemsCount": n})

    def sync_zreports(self, n=32):
        return self._call(f"/DataBase/Files/Sync/ZReports/{self._need_fid()}", "POST", params={"ItemsCount": n})

    def pos_lock(self):
        if not self.secret or len(self.secret) != 64:
            raise FiscalError("POS-ключ должен быть 64 hex-символа (32 байта)")
        key = self.secret + hashlib.sha256(bytes.fromhex(self.secret)).hexdigest()
        return self._call(f"/POS/Lock/{self._need_fid()}", "POST", params={"SecretKey": key}, body={"SecretKey": key})


def get_client(settings):
    mode = settings.get("fiscal_mode", "mock")
    if mode == "service":
        return ServiceFiscal(settings.get("fiscal_url"), settings.get("fiscal_factory_id"),
                             settings.get("fiscal_pos_secret", ""))
    if mode == "mock":
        return MockFiscal()
    return BaseFiscal()
