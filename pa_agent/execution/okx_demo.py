"""Small OKX V5 REST boundary. Demo header and origin cannot be configured away."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import stat
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import time
from urllib.parse import urlencode

import httpx

from pa_agent.execution.demo_contracts import (
    DemoSnapshot,
    DemoContract,
    DemoOrderRequest,
    canonical,
)


class DemoRequestRejected(RuntimeError):
    def __init__(self, code: str):
        self.code = code if re.fullmatch(r"[0-9]{1,8}", str(code)) else "invalid_code"
        super().__init__(f"demo_request_rejected:{self.code}")


class DemoRequestUnknown(RuntimeError):
    def __init__(self, operation: str):
        super().__init__(f"demo_request_unknown:{operation}")


class DemoUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class OkxDemoCredentials:
    key: str = field(repr=False)
    secret: str = field(repr=False)
    passphrase: str = field(repr=False)


def _private_systemd_acl(path: Path, permission: int) -> bool:
    """A group mode bit can be an ACL mask, with no actual group access."""
    try:
        metadata = path.stat()
        expected_type = stat.S_ISDIR if permission == 5 else stat.S_ISREG
        if path.is_symlink() or metadata.st_uid != 0 or not expected_type(metadata.st_mode):
            return False
        if stat.S_IMODE(metadata.st_mode) != (0o550 if permission == 5 else 0o440):
            return False
        raw = os.getxattr(path, "system.posix_acl_access")
        if len(raw) != 44 or struct.unpack("<I", raw[:4])[0] != 2:
            return False
        entries = [struct.unpack("<HHI", raw[i : i + 8]) for i in range(4, 44, 8)]
        undefined = 0xFFFFFFFF
        expected = [
            (1, permission, undefined),
            (2, permission, os.geteuid()),
            (4, 0, undefined),
            (16, permission, undefined),
            (32, 0, undefined),
        ]
        return sorted(entries) == sorted(expected)
    except (OSError, AttributeError, ValueError, struct.error):
        return False


def _systemd_credential_permissions(path: Path, directory: Path) -> bool:
    configured = os.environ.get("CREDENTIALS_DIRECTORY")
    return bool(
        configured
        and Path(configured).resolve() == directory.resolve()
        and _private_systemd_acl(directory, 5)
        and _private_systemd_acl(path, 4)
    )


def load_demo_credentials(directory: Path) -> OkxDemoCredentials:
    values = []
    for name in ("okx-demo-key", "okx-demo-secret", "okx-demo-passphrase"):
        path = directory / name
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
            raise DemoUnavailable("invalid_credential_file")
        if (
            os.name != "nt"
            and stat.S_IMODE(path.stat().st_mode) & 0o077
            and not _systemd_credential_permissions(path, directory)
        ):
            raise DemoUnavailable("credential_permissions")
        value = path.read_text(encoding="utf-8").strip()
        if not value or any(c in value for c in "\r\n"):
            raise DemoUnavailable("invalid_credential_value")
        values.append(value)
    return OkxDemoCredentials(*values)


# Deliberately omit messages, API permissions, UID and response headers from persisted rows.
ROW_KEYS = frozenset(
    "instId instType ordId clOrdId algoId algoClOrdId ordType state side posSide tdMode "
    "px sz accFillSz avgPx fillSz fillPx fillTime tradeId billId ts uTime cTime pnl fee feeCcy "
    "fundingFee balChg ccy type subType pos upl imr margin mgnMode lever reduceOnly closeFraction "
    "slTriggerPx tpTriggerPx slTriggerPxType tpTriggerPxType slOrdPx tpOrdPx actualSz actualPx "
    "actualSide actualOrdId triggerTime sCode".split()
)


def safe_row(value: dict) -> dict[str, str]:
    result = {
        k: str(v)[:160]
        for k, v in value.items()
        if k in ROW_KEYS and isinstance(v, (str, int)) and not isinstance(v, bool)
    }
    ids = value.get("ordIdList")
    if (
        isinstance(ids, list)
        and 0 < len(ids) <= 16
        and all(isinstance(v, str) and v.isalnum() and len(v) <= 64 for v in ids)
    ):
        result["child_order_ids"] = canonical(ids)
    return result


def _raise_code(code, operation):
    code = str(code)
    # OKX 50004 explicitly means the operation may have succeeded. Treat all
    # internal-service codes conservatively; only definite business refusals end a leg.
    if not re.fullmatch(r"[0-9]{1,8}", code) or 50000 <= int(code) <= 50099:
        raise DemoRequestUnknown(operation)
    raise DemoRequestRejected(code)


class OkxDemoClient:
    ORIGIN = "https://openapi.okx.com"

    def __init__(
        self,
        credentials: OkxDemoCredentials,
        *,
        transport=None,
        now_ms=None,
        instruments=("BTC-USDT-SWAP",),
    ):
        self.credentials = credentials
        self.now_ms = now_ms or (lambda: int(time() * 1000))
        self.instruments = tuple(instruments)
        self.http = httpx.Client(
            transport=transport, timeout=5, follow_redirects=False, trust_env=False
        )

    def _request(self, method, path, *, query=None, body=None, private=True, exp_time_ms=None):
        suffix = path + ("?" + urlencode(query) if query else "")
        text = canonical(body) if body is not None else ""
        headers = {"Content-Type": "application/json", "x-simulated-trading": "1"}
        if private:
            stamp = (
                datetime.fromtimestamp(self.now_ms() / 1000, timezone.utc)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z")
            )
            signature = base64.b64encode(
                hmac.new(
                    self.credentials.secret.encode(),
                    (stamp + method + suffix + text).encode(),
                    hashlib.sha256,
                ).digest()
            ).decode()
            headers.update(
                {
                    "OK-ACCESS-KEY": self.credentials.key,
                    "OK-ACCESS-SIGN": signature,
                    "OK-ACCESS-PASSPHRASE": self.credentials.passphrase,
                    "OK-ACCESS-TIMESTAMP": stamp,
                }
            )
        if exp_time_ms is not None:
            if self.now_ms() >= exp_time_ms:
                raise DemoUnavailable("request_expired")
            headers["expTime"] = str(exp_time_ms)
        try:
            response = self.http.request(
                method,
                self.ORIGIN + suffix,
                headers=headers,
                content=text.encode() if text else None,
            )
            if response.status_code != 200 or len(response.content) > 4 * 1024 * 1024:
                raise ValueError("invalid_response")
            value = response.json()
            if not isinstance(value, dict) or not isinstance(value.get("data"), list):
                raise ValueError("invalid_response")
        except (httpx.HTTPError, ValueError, TypeError):
            raise DemoRequestUnknown(path) from None
        code = str(value.get("code", ""))
        if code != "0":
            _raise_code(code, path)
        rows = value["data"]
        if any(not isinstance(row, dict) for row in rows):
            raise DemoRequestUnknown(path)
        for row in rows:
            if str(row.get("sCode", "0")) != "0":
                _raise_code(row["sCode"], path)
        return rows

    def _post(self, path, body, required, *, exp_time_ms=None):
        rows = self._request("POST", path, body=body, exp_time_ms=exp_time_ms)
        if len(rows) != 1 or not rows[0].get(required):
            raise DemoRequestUnknown(path)
        return safe_row(rows[0])

    def place_limit(self, request: DemoOrderRequest):
        return self._post(
            "/api/v5/trade/order",
            dict(
                instId=request.inst_id,
                tdMode="isolated",
                posSide="net",
                side=request.side,
                ordType="limit",
                px=str(request.px),
                sz=str(request.sz),
                clOrdId=request.client_id,
            ),
            "ordId",
            exp_time_ms=request.exp_time_ms,
        )

    def get_order(self, inst_id, client_id=None, *, order_id=None):
        identity = {"ordId": order_id} if order_id else {"clOrdId": client_id}
        rows = self._request("GET", "/api/v5/trade/order", query={"instId": inst_id, **identity})
        return safe_row(rows[0]) if len(rows) == 1 else {}

    def cancel_order(self, inst_id, client_id):
        return self._post(
            "/api/v5/trade/cancel-order", {"instId": inst_id, "clOrdId": client_id}, "ordId"
        )

    def place_oco(self, *, inst_id, side, stop, tp1, client_id):
        return self._post(
            "/api/v5/trade/order-algo",
            dict(
                instId=inst_id,
                tdMode="isolated",
                posSide="net",
                side=side,
                ordType="oco",
                algoClOrdId=client_id,
                reduceOnly=True,
                closeFraction="1",
                slTriggerPx=str(stop),
                tpTriggerPx=str(tp1),
                slOrdPx="-1",
                tpOrdPx="-1",
                slTriggerPxType="last",
                tpTriggerPxType="last",
            ),
            "algoId",
        )

    def get_algo(self, inst_id, client_id):
        rows = self._request("GET", "/api/v5/trade/order-algo", query={"algoClOrdId": client_id})
        return safe_row(rows[0]) if len(rows) == 1 and rows[0].get("instId") == inst_id else {}

    def cancel_algo(self, inst_id, algo_id):
        return self._post(
            "/api/v5/trade/cancel-algos", [{"instId": inst_id, "algoId": algo_id}], "algoId"
        )

    def close_position(self, *, inst_id, side, quantity, client_id):
        return self._post(
            "/api/v5/trade/order",
            dict(
                instId=inst_id,
                tdMode="isolated",
                posSide="net",
                side=side,
                ordType="market",
                sz=str(quantity),
                reduceOnly=True,
                clOrdId=client_id,
            ),
            "ordId",
        )

    def _pages(self, path, query, key, since=None):
        rows, seen, cursor = [], set(), None
        for _ in range(4):
            batch = self._request(
                "GET", path, query=query | {"limit": "100"} | ({"after": cursor} if cursor else {})
            )
            reached = False
            for raw in batch:
                raw_id = str(raw.get(key) or "")
                identity = (str(raw.get("instId")) + ":" + raw_id) if key == "tradeId" else raw_id
                if not raw_id or (key == "tradeId" and not raw.get("instId")):
                    return rows, False
                if (str(raw.get("billId")) if key == "tradeId" else raw_id) == since:
                    reached = True
                    break
                if identity not in seen:
                    rows.append(safe_row(raw))
                    seen.add(identity)
            if reached or len(batch) < 100:
                return rows, reached or since is None
            next_cursor = str(
                batch[-1].get("billId") or (batch[-1].get(key) if key != "tradeId" else "") or ""
            )
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return rows, False

    def snapshot(self, *, since_bill_id=None, since_fill_ids=None):
        start = self.now_ms()
        server = self._request("GET", "/api/v5/public/time", private=False)
        config = self._request("GET", "/api/v5/account/config")
        balance = self._request("GET", "/api/v5/account/balance", query={"ccy": "USDT"})
        if len(server) != 1 or len(config) != 1 or not config[0].get("uid"):
            raise DemoUnavailable("incomplete_account_identity")
        positions = self._request("GET", "/api/v5/account/positions")
        orders, orders_ok = self._pages("/api/v5/trade/orders-pending", {}, "ordId")
        algos, algos_ok = [], True
        for kind in ("oco", "conditional", "trigger", "move_order_stop", "iceberg", "twap"):
            page, covered = self._pages(
                "/api/v5/trade/orders-algo-pending", {"ordType": kind}, "algoId"
            )
            algos.extend(page)
            algos_ok &= covered
        fills, fills_ok = self._pages(
            "/api/v5/trade/fills",
            {"instType": "SWAP"},
            "tradeId",
            (since_fill_ids or {}).get("SWAP"),
        )
        bills, bills_ok = self._pages("/api/v5/account/bills", {}, "billId", since_bill_id)
        available = self._request("GET", "/api/v5/account/instruments", query={"instType": "SWAP"})
        contracts, leverages, quotes = [], {}, {}
        for row in available:
            inst = row.get("instId")
            if inst not in self.instruments or row.get("state") != "live":
                continue
            contracts.append(
                DemoContract(
                    inst_id=inst,
                    ct_type=row.get("ctType"),
                    settle_ccy=row.get("settleCcy"),
                    tick=row.get("tickSz"),
                    lot=row.get("lotSz"),
                    minimum=row.get("minSz"),
                    maximum_limit=row.get("maxLmtSz"),
                    maximum_market=row.get("maxMktSz"),
                    ct_val=row.get("ctVal"),
                    ct_mult=row.get("ctMult") or "1",
                    observed_at_ms=start,
                )
            )
            leverage = self._request(
                "GET",
                "/api/v5/account/leverage-info",
                query={"instId": inst, "mgnMode": "isolated"},
            )
            if len(leverage) == 1 and leverage[0].get("posSide") == "net":
                leverages[inst] = leverage[0].get("lever", "")
            ticker = self._request(
                "GET", "/api/v5/market/ticker", query={"instId": inst}, private=False
            )
            if len(ticker) == 1 and ticker[0].get("instId") == inst:
                quotes[inst] = {k: str(ticker[0].get(k, "")) for k in ("askPx", "bidPx", "ts")}
        details = balance[0].get("details", []) if len(balance) == 1 else []
        usdt = next((d for d in details if d.get("ccy") == "USDT"), {})
        return DemoSnapshot(
            observed_at_ms=start,
            server_time_ms=int(server[0]["ts"]),
            account_key=hashlib.sha256(
                ("okx_demo_v1:" + str(config[0]["uid"])).encode()
            ).hexdigest(),
            account_mode=str(config[0].get("acctLv", "")),
            position_mode=str(config[0].get("posMode", "")),
            equity=usdt.get("eq") or None,
            available=usdt.get("availBal") or None,
            contracts=tuple(contracts),
            leverage=leverages,
            quotes=quotes,
            orders=tuple(orders),
            algos=tuple(algos),
            positions=tuple(safe_row(r) for r in positions),
            fills=tuple(fills),
            bills=tuple(bills),
            coverage={
                "orders": orders_ok,
                "algos": algos_ok,
                "fills": fills_ok,
                "bills": bills_ok,
                "positions": True,
                "account": bool(usdt),
                "contracts": bool(contracts),
            },
        )

    def close(self):
        self.http.close()
