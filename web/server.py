"""
XuanWu - may chu web cuc bo de choi voi engine.

Chay:  python3 web/chay.py
Tat:   Ctrl+C

Dung http.server co san trong Python, khong cai them thu vien nao. Web nay chi
phuc vu MOT nguoi choi tren may cua chinh minh nen khong can may chu chiu tai;
bot mot thu vien la bot mot thu co the hong khi mo lai sau vai tuan.

API duoc thiet ke de mo rong sau nay ma khong phai sua kien truc:
  POST /api/van-moi    tao van moi
  POST /api/di         nguoi di mot nuoc, may tra loi
  POST /api/danh-gia   cham diem the co hien tai (cho thanh danh gia)
  POST /api/goi-y      goi y nuoc di tot nhat (khong di)
  POST /api/phan-tich  (chua lam) phan tich ca van
"""

import http.server
import json
import os
import socketserver
import sys
import threading
import time
import webbrowser

THU_MUC = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(THU_MUC))

from engine.board import WHITE, BLACK, start_board
from engine import c_core, book
from engine.evaluate import evaluate as danh_gia_tinh
from engine.game_rules import VanCo, DANG_CHOI
from engine import strongest
from tools.collect_openings import board_to_fen, fen_to_board

CONG = 8000
MUC_DO = {"de": 0.5, "vua": 3.0, "kho": 10.0}

# Cac van dang choi, theo ma van. Giu trong bo nho vi web chi chay khi can.
_van = {}
_khoa = threading.Lock()

# Phan loi C KHONG an toan da luong: no dung bien toan cuc cho bang chuyen vi,
# ngan xep tich luy va bo dem nut. Tu khi doi sang may chu da luong (de may
# khac vao duoc), hai yeu cau chay cung luc se giam len nhau va treo.
# Khoa nay bat buoc moi lan goi engine phai xep hang.
_khoa_engine = threading.Lock()


def _ban_co_json(v: VanCo):
    """Ban co dang mang 10x9 ky tu, kem trang thai van."""
    tt, ly_do = v.trang_thai()
    return {
        "ban_co": ["".join(h) for h in v.board],
        "ben_di": "trang" if v.side == WHITE else "den",
        "bi_chieu": v.dang_bi_chieu(),
        "trang_thai": tt,
        "ly_do": ly_do,
        "nuoc_hop_le": [list(m) for m in v.nuoc_hop_le()] if tt == DANG_CHOI else [],
        "so_nuoc": len(v.lich_su) - 1,
        "fen": board_to_fen(v.board, v.side),
    }


def _cham_diem(v: VanCo, giay: float = 0.3):
    """Diem 0..1000 goc nhin Trang, dung cho thanh danh gia."""
    # The co khoi dau la MOC CHUAN cua thang diem: 500 can bang + 5 tempo.
    # Tim kiem co the tra 503 hay 506 va deu dung, nhung moc chuan thi phai
    # co dinh nen chot cung o day.
    if len(v.lich_su) == 1 and v.side == WHITE:
        return 505
    tt, _ = v.trang_thai()
    if tt == "trang_thang":
        return 1000
    if tt == "den_thang":
        return 0
    if tt == "hoa":
        return 500
    # Diem cho thanh danh gia: dung DO SAU CO DINH thay vi thoi gian, de cung
    # mot the co luon cho cung mot so. Truoc day dung thoi gian nen bam nhieu
    # lan ra 503, 502, 506... trong nhu engine khong on dinh.
    with _khoa_engine:
        diem, _, _ = strongest.tim_nuoc_di(v.board, v.side, depth=6,
                                           dung_sach=False)
    return diem


def _dia_chi_lan(cong):
    """Dia chi de may khac trong cung mang WiFi vao duoc."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return f"http://{ip}:{cong}/"
    except Exception:
        return None


class May(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=THU_MUC, **kw)

    def log_message(self, *a):
        pass                                  # khong in log moi yeu cau

    def _tra(self, data, ma=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(ma)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._tra({"loi": "JSON khong hop le"}, 400)
        duong = self.path.split("?")[0]
        try:
            if duong == "/api/van-moi":
                return self._van_moi(req)
            if duong == "/api/di":
                return self._di(req)
            if duong == "/api/danh-gia":
                return self._danh_gia(req)
            if duong == "/api/goi-y":
                return self._goi_y(req)
            if duong == "/api/phan-tich":
                return self._phan_tich(req)
            if duong == "/api/lui":
                return self._lui(req)
            if duong == "/api/nap-fen":
                return self._nap_fen(req)
            if duong == "/api/dia-chi":
                return self._tra({"lan": _dia_chi_lan(CONG)})
            return self._tra({"loi": "khong co duong dan nay"}, 404)
        except Exception as e:                # tra loi ro thay vi treo trang
            return self._tra({"loi": f"{type(e).__name__}: {e}"}, 500)

    # -- cac diem cuoi -----------------------------------------------------

    def _van_moi(self, req):
        ma = str(int(time.time() * 1000))
        with _khoa:
            _van[ma] = VanCo()
        v = _van[ma]
        nuoc_may = None
        # Nguoi cam Den -> may (Do) di truoc ngay
        if req.get("nguoi_cam") == "den":
            giay = MUC_DO.get(req.get("muc_do", "vua"), 3.0)
            with _khoa_engine:
                _, nuoc_may, _, _ = strongest.tim_nuoc_di_theo_gio(v.board, v.side, giay)
            if nuoc_may:
                v.di(nuoc_may)
        return self._tra({"ma_van": ma, **_ban_co_json(v),
                          "diem": _cham_diem(v, 0.25),
                          "nuoc_may": list(nuoc_may) if nuoc_may else None})

    def _di(self, req):
        ma = req.get("ma_van")
        with _khoa:
            v = _van.get(ma)
        if v is None:
            return self._tra({"loi": "khong tim thay van"}, 404)

        # 1. Nguoi di
        mv = tuple(req["nuoc"])
        try:
            v.di(mv)
        except ValueError as e:
            return self._tra({"loi": str(e)}, 400)

        tt, _ = v.trang_thai()
        if tt != DANG_CHOI:
            return self._tra({**_ban_co_json(v), "diem": _cham_diem(v),
                              "nuoc_may": None})

        # Che do tu choi hai ben: nguoi di het, may khong tra loi
        if req.get("tu_choi"):
            d = _cham_diem(v)
            return self._tra({**_ban_co_json(v), "diem": d,
                              "diem_nuoc_nguoi": d, "nuoc_may": None})

        # 2. May tra loi
        giay = MUC_DO.get(req.get("muc_do", "vua"), 3.0)
        t0 = time.time()
        with _khoa_engine:
            diem, nuoc_may, nut, do_sau = strongest.tim_nuoc_di_theo_gio(
                v.board, v.side, giay)
        if nuoc_may is None:
            return self._tra({**_ban_co_json(v), "diem": _cham_diem(v),
                              "nuoc_may": None})
        # `diem` la diem SAU nuoc nguoi, TRUOC nuoc may - dung de cham chat
        # luong nuoc nguoi vua di. Thanh danh gia thi phai hien diem sau nuoc
        # may. Truoc day tra ve mot con so cho ca hai viec nen cham nuoc bi tre
        # mot nhip: phai doi nuoc sau moi biet nuoc truoc tot hay xau.
        diem_nuoc_nguoi = diem
        v.di(nuoc_may)
        return self._tra({
            **_ban_co_json(v),
            "diem": _cham_diem(v, 0.25),
            "diem_nuoc_nguoi": diem_nuoc_nguoi,
            "nuoc_may": list(nuoc_may),
            "do_sau": do_sau,
            "so_nut": nut,
            "giay": round(time.time() - t0, 2),
        })

    def _goi_y(self, req):
        """Nuoc di tot nhat cho ben dang di, KHONG thuc hien. Dung ve mui ten."""
        with _khoa:
            v = _van.get(req.get("ma_van"))
        if v is None:
            return self._tra({"loi": "khong tim thay van"}, 404)
        tt, _ = v.trang_thai()
        if tt != DANG_CHOI:
            return self._tra({"nuoc": None, "diem": _cham_diem(v)})
        giay = MUC_DO.get(req.get("muc_do", "vua"), 3.0)
        with _khoa_engine:
            diem, nuoc, nut, do_sau = strongest.tim_nuoc_di_theo_gio(
                v.board, v.side, giay)
        return self._tra({
            "nuoc": list(nuoc) if nuoc else None,
            "diem": diem, "do_sau": do_sau, "so_nut": nut,
            "ben": "trang" if v.side == WHITE else "den",
        })

    def _phan_tich(self, req):
        """Phan tich day du mot the co: diem, nuoc tot nhat, bien chinh, so nut."""
        with _khoa:
            v = _van.get(req.get("ma_van"))
        if v is None:
            return self._tra({"loi": "khong tim thay van"}, 404)
        tt, ly_do = v.trang_thai()
        goc = {**_ban_co_json(v), "fen": board_to_fen(v.board, v.side)}
        if tt != DANG_CHOI:
            return self._tra({**goc, "diem": _cham_diem(v), "nuoc_tot": None})
        giay = MUC_DO.get(req.get("muc_do", "vua"), 3.0)
        t0 = time.time()
        with _khoa_engine:
            diem, nuoc, nut, do_sau = strongest.tim_nuoc_di_theo_gio(
                v.board, v.side, giay, dung_sach=False)
            # Bien chinh phai lay TRONG cung khoa: no doc bang chuyen vi, ma
            # yeu cau khac co the ghi de bang do neu ta nha khoa ra truoc.
            bien = c_core.bien_chinh(v.board, v.side) if c_core.co_loi_c() else []
        dt = time.time() - t0
        # Kiem tra the co nay co trong sach khai cuoc khong
        from engine.search import board_hash
        trong_sach = book.tra_sach(v.board, v.side,
                                   board_hash(v.board, v.side)) is not None
        return self._tra({
            **goc,
            "diem": diem,
            "diem_tinh": danh_gia_tinh(v.board, v.side),
            "nuoc_tot": list(nuoc) if nuoc else None,
            "bien_chinh": [list(m) for m in bien],
            "do_sau": do_sau, "so_nut": nut,
            "giay": round(dt, 2),
            "nut_moi_giay": int(nut / dt) if dt > 0 else 0,
            "trong_sach": trong_sach,
        })

    def _lui(self, req):
        """Lui lai mot hoac hai nuoc. Dung lai van tu dau cho don gian va chac."""
        ma = req.get("ma_van")
        with _khoa:
            v = _van.get(ma)
        if v is None:
            return self._tra({"loi": "khong tim thay van"}, 404)
        so_lui = max(1, int(req.get("so_nuoc", 1)))
        cac_nuoc = req.get("cac_nuoc", [])
        giu = cac_nuoc[:max(0, len(cac_nuoc) - so_lui)]
        moi = VanCo()
        for mv in giu:
            try:
                moi.di(tuple(mv))
            except ValueError:
                break
        with _khoa:
            _van[ma] = moi
        return self._tra({**_ban_co_json(moi), "diem": _cham_diem(moi, 0.25),
                          "fen": board_to_fen(moi.board, moi.side)})

    def _nap_fen(self, req):
        ma = str(int(time.time() * 1000))
        try:
            b, s = fen_to_board(req["fen"].strip())
        except Exception as e:
            return self._tra({"loi": f"FEN khong hop le: {e}"}, 400)
        with _khoa:
            _van[ma] = VanCo(b, s)
        v = _van[ma]
        return self._tra({"ma_van": ma, **_ban_co_json(v),
                          "diem": _cham_diem(v, 0.25),
                          "fen": board_to_fen(v.board, v.side)})

    def _danh_gia(self, req):
        with _khoa:
            v = _van.get(req.get("ma_van"))
        if v is None:
            return self._tra({"loi": "khong tim thay van"}, 404)
        return self._tra({"diem": _cham_diem(v, req.get("giay", 0.3))})


def main():
    print("XuanWu - dang khoi dong...")
    ok = strongest.chuan_bi()
    print(f"  engine: {'loi C' if ok else 'Python thuan (cham hon)'}")
    from engine import book
    print(f"  sach khai cuoc: {book.so_muc():,} the co" if book.nap()
          else "  sach khai cuoc: khong co")
    socketserver.TCPServer.allow_reuse_address = True
    # Lang nghe tren moi dia chi de may khac trong cung mang WiFi vao duoc.
    # Chi trong mang noi bo, khong ra Internet - an toan cho may ca nhan.
    with socketserver.ThreadingTCPServer(("0.0.0.0", CONG), May) as may:
        dia_chi = f"http://127.0.0.1:{CONG}/"
        import socket
        try:
            s_ = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s_.connect(("8.8.8.8", 80))
            ip = s_.getsockname()[0]
            s_.close()
        except Exception:
            ip = None
        print(f"\n  May nay      : {dia_chi}")
        if ip:
            print(f"  May khac     : http://{ip}:{CONG}/   (cung mang WiFi)")
        print("  Nhan Ctrl+C de tat\n")
        threading.Timer(0.8, lambda: webbrowser.open(dia_chi)).start()
        try:
            may.serve_forever()
        except KeyboardInterrupt:
            print("\nDa tat.")


if __name__ == "__main__":
    main()
