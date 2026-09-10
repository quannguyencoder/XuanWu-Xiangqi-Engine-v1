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
import random
import re
import secrets
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
from engine.game_rules import VanCo, DANG_CHOI, TRANG_THANG, DEN_THANG
from engine import strongest
from tools.collect_openings import board_to_fen, fen_to_board, iccs_to_move

# Render (va da so nen tang hosting) cap cong qua bien moi truong PORT thay
# vi de co dinh - doc bien do neu co, mac dinh 8000 nhu chay tren may ca nhan.
CONG = int(os.environ.get("PORT", 8000))
MUC_DO = {"de": 0.5, "vua": 3.0, "kho": 10.0}

# Bai tap chien thuat: khai thac tu 16 trieu the co huan luyen da co san,
# khong ton them chi phi gi - xem tools/mine_puzzles.py de biet cach lam.
# Nap MOT LAN luc khoi dong, giu trong bo nho (chi ~2MB, khong dang lo).
_DUONG_PUZZLE = os.path.join(os.path.dirname(THU_MUC), "data", "puzzles.jsonl")
_puzzles = []


def _nap_puzzles():
    global _puzzles
    if _puzzles or not os.path.exists(_DUONG_PUZZLE):
        return
    with open(_DUONG_PUZZLE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                _puzzles.append(json.loads(line))
            except json.JSONDecodeError:
                continue


# Kho the co khai cuoc THAT tu chessdb.cn (371.855 the, xem
# tools/crawl_chessdb.py). CHI CO FEN - khong co ten khai cuoc hay ti le
# thang, vi day von la du lieu diem xuat phat cho Pikafish tu choi, khong
# phai kho du lieu khai cuoc co chu thich. Duyet duoc that, nhung khong bia
# them thong ke khong co.
_DUONG_KHAI_CUOC = os.path.join(os.path.dirname(THU_MUC), "data", "seeds_chessdb.jsonl")
_khai_cuoc = []


def _nap_khai_cuoc():
    global _khai_cuoc
    if _khai_cuoc or not os.path.exists(_DUONG_KHAI_CUOC):
        return
    with open(_DUONG_KHAI_CUOC, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                if d.get("fen"):
                    _khai_cuoc.append(d["fen"])
            except json.JSONDecodeError:
                continue

# Van DA KET THUC duoc luu rieng ra file JSON (khong lien quan gi den _van o
# duoi - do la ban dang choi DO trong RAM). Moi van la mot file, don gian hon
# CSDL that va du dung cho mot may ca nhan. Ma van luon dung dinh dang
# "<so>-<hex>" (xem _luu_van_xong) - _MA_VAN_HOP_LE dung de loc truoc khi ghep
# vao duong dan file, tranh path traversal tu du lieu client gui len.
THU_MUC_VAN = os.path.join(os.path.dirname(THU_MUC), "data", "games")
_MA_VAN_HOP_LE = re.compile(r"^[0-9]+-[0-9a-f]{6}$")

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
    """Dia chi de may khac trong cung mang WiFi vao duoc: ca IP so va ten
    .local (Bonjour/mDNS macOS phat san, khong can cau hinh gi).

    Ten .local de nho hon va KHONG DOI khi router cap lai IP, nhung chi chac
    chan vao duoc tu thiet bi Apple khac (iPhone/iPad/Mac) - Android va Windows
    ho tro mDNS khong on dinh. Vi vay tra ca hai, IP van la phuong an chac an.
    """
    import socket
    ten_local = None
    try:
        h = socket.gethostname()
        ten_local = h if h.endswith(".local") else f"{h}.local"
    except Exception:
        pass
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        return None
    return {
        "ip": f"http://{ip}:{cong}/",
        "local": f"http://{ten_local}:{cong}/" if ten_local else None,
    }


def _tranh_tu_thua(v: VanCo, nuoc, giay: float):
    """Doi nuoc khac neu nuoc engine chon khien chinh no bi xu THUA.

    Engine tim kiem chi biet the co roi rac, khong biet luat chieu lien tuc -
    luat do phu thuoc LICH SU van co. Nen o muc de (0,5 giay) no hay chieu di
    chieu lai roi tu thua ma khong hieu tai sao.

    Cach chua: thu nuoc engine chon, neu ket qua la chinh no thua thi bo va lay
    nuoc tot ke tiep. Thu toi da 6 nuoc roi danh chiu.
    """
    if nuoc is None:
        return None
    minh = v.side
    thua_cua_minh = DEN_THANG if minh == WHITE else TRANG_THANG

    def tu_thua(mv):
        thu = VanCo(v.board, v.side)
        thu.lich_su = list(v.lich_su)
        thu.tu_lan_an_quan = v.tu_lan_an_quan
        try:
            thu.di(mv)
        except ValueError:
            return True
        return thu.trang_thai()[0] == thua_cua_minh

    if not tu_thua(nuoc):
        return nuoc
    # Nuoc tot nhat tu thua -> xep hang cac nuoc con lai theo diem, lay nuoc dau
    # tien khong tu thua.
    con_lai = [m for m in v.nuoc_hop_le() if m != nuoc and not tu_thua(m)]
    if not con_lai:
        return nuoc                       # moi nuoc deu thua, danh chiu
    tot, diem_tot = con_lai[0], None
    with _khoa_engine:
        for m in con_lai[:6]:
            con = VanCo(v.board, v.side)
            con.di(m)
            d, _, _ = strongest.tim_nuoc_di(con.board, con.side, depth=4,
                                            dung_sach=False)
            # minh la Trang thi muon diem CAO, la Den thi muon diem THAP
            if diem_tot is None or (d > diem_tot if minh == WHITE else d < diem_tot):
                tot, diem_tot = m, d
    return tot


class May(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=THU_MUC, **kw)

    def log_message(self, *a):
        pass                                  # khong in log moi yeu cau

    def end_headers(self):
        # Bao trinh duyet DUNG luu dem. Khong co dong nay thi doi tep am thanh
        # hay sua giao dien xong van thay ban cu, vi trinh duyet dung ban da luu
        # theo ten tep.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

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
            if duong == "/api/may-di":
                return self._may_di(req)
            if duong == "/api/nap-fen":
                return self._nap_fen(req)
            if duong == "/api/dia-chi":
                return self._tra({"lan": _dia_chi_lan(CONG)})
            if duong == "/api/luu-van-xong":
                return self._luu_van_xong(req)
            if duong == "/api/danh-sach-van":
                return self._danh_sach_van(req)
            if duong == "/api/xem-van":
                return self._xem_van(req)
            if duong == "/api/xoa-van":
                return self._xoa_van(req)
            if duong == "/api/giai-the-moi":
                return self._giai_the_moi(req)
            if duong == "/api/duyet-khai-cuoc":
                return self._duyet_khai_cuoc(req)
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
        nuoc_may = _tranh_tu_thua(v, nuoc_may, giay)
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

    def _may_di(self, req):
        """Cho may di mot nuoc. Dung sau khi nguoi choi lui ve dung luot may."""
        with _khoa:
            v = _van.get(req.get("ma_van"))
        if v is None:
            return self._tra({"loi": "khong tim thay van"}, 404)
        if v.trang_thai()[0] != DANG_CHOI:
            return self._tra({**_ban_co_json(v), "diem": _cham_diem(v),
                              "nuoc_may": None})
        giay = MUC_DO.get(req.get("muc_do", "vua"), 3.0)
        with _khoa_engine:
            _, nuoc, _, _ = strongest.tim_nuoc_di_theo_gio(v.board, v.side, giay)
        nuoc = _tranh_tu_thua(v, nuoc, giay)
        if nuoc:
            v.di(nuoc)
        return self._tra({**_ban_co_json(v), "diem": _cham_diem(v, 0.25),
                          "nuoc_may": list(nuoc) if nuoc else None})

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
        # The co khoi dau la MOC CHUAN cua thang diem (500 can bang + 5 tempo).
        # Tim kiem tra 496 hay 503 deu dung, nhung moc chuan thi phai co dinh.
        # Chi ghi de rieng truong diem, van giu nguyen phan tich de o "nuoc tot
        # nhat" khong bi trong.
        if len(v.lich_su) == 1 and v.side == WHITE:
            diem = 505
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
        """Lui lai mot hoac nhieu nuoc. Dung lai van tu dau cho don gian va chac.

        so_nuoc=0 la truong hop dac biet: phat lai TOAN BO cac_nuoc khong bo
        gi ca. Dung khi web client KHOI PHUC van dang choi sau khi tai lai
        trang (F5) - client chi luu duoc danh sach nuoc di (khong co CSDL o
        server), nen phai tao van moi roi "lui 0 nuoc" voi ca lich su de dung
        lai dung trang thai cu.

        fen_goc (tuy chon): the co XUAT PHAT de phat lai tren do, thay vi the
        co khoi dau chuan. Can cho truong hop khoi phuc mot van bat dau tu FEN
        tuy y (nap-fen) roi nguoi choi da di tiep vai nuoc truoc khi tai trang -
        neu khong co tham so nay, phat lai se sai vi lai bat dau tu ban co
        chuan thay vi dung FEN da nap.
        """
        ma = req.get("ma_van")
        with _khoa:
            v = _van.get(ma)
        if v is None:
            return self._tra({"loi": "khong tim thay van"}, 404)
        so_lui = max(0, int(req.get("so_nuoc", 1)))
        cac_nuoc = req.get("cac_nuoc", [])
        giu = cac_nuoc[:max(0, len(cac_nuoc) - so_lui)]
        fen_goc = req.get("fen_goc")
        if fen_goc:
            try:
                b0, s0 = fen_to_board(fen_goc)
                moi = VanCo(b0, s0)
            except Exception:
                moi = VanCo()
        else:
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

    # -- luu tru van da ket thuc (xem lai kieu chess.com) -------------------
    # Khong dung CSDL that: moi van la mot file JSON trong data/games/. Client
    # tu goi /api/luu-van-xong MOT LAN khi phat hien van vua ket thuc - server
    # khong tu dong luu, vi no khong giu du lieu diem-tung-nuoc (lich_su_diem)
    # ma chi client moi co.

    def _luu_van_xong(self, req):
        os.makedirs(THU_MUC_VAN, exist_ok=True)
        ma = f"{int(time.time()*1000)}-{secrets.token_hex(3)}"
        ban_ghi = {
            "ma": ma,
            "luc": time.time(),
            "ten": str(req.get("ten") or "")[:40],
            "ben_cam": req.get("ben_cam"),
            "che_do": req.get("che_do"),
            "muc_do": req.get("muc_do"),
            "ket_qua": req.get("ket_qua"),
            "ly_do": req.get("ly_do"),
            "fen_goc": req.get("fen_goc"),
            "lich_su": req.get("lich_su") or [],
            "lich_su_diem": req.get("lich_su_diem") or [],
        }
        with open(os.path.join(THU_MUC_VAN, f"{ma}.json"), "w", encoding="utf-8") as f:
            json.dump(ban_ghi, f, ensure_ascii=False)
        return self._tra({"ma": ma})

    def _danh_sach_van(self, req):
        os.makedirs(THU_MUC_VAN, exist_ok=True)
        ra = []
        for ten_tep in os.listdir(THU_MUC_VAN):
            if not ten_tep.endswith(".json"):
                continue
            try:
                with open(os.path.join(THU_MUC_VAN, ten_tep), encoding="utf-8") as f:
                    d = json.load(f)
            except Exception:
                continue
            tom_tat = {k: d.get(k) for k in
                       ("ma", "luc", "ten", "ben_cam", "che_do", "muc_do",
                        "ket_qua", "ly_do")}
            tom_tat["so_nuoc"] = len(d.get("lich_su") or [])
            ra.append(tom_tat)
        ra.sort(key=lambda x: x.get("luc") or 0, reverse=True)
        gioi_han = min(500, max(1, int(req.get("gioi_han", 200))))
        return self._tra({"ds": ra[:gioi_han]})

    def _xem_van(self, req):
        ma = req.get("ma", "")
        if not _MA_VAN_HOP_LE.match(ma):
            return self._tra({"loi": "ma khong hop le"}, 400)
        duong = os.path.join(THU_MUC_VAN, f"{ma}.json")
        if not os.path.exists(duong):
            return self._tra({"loi": "khong tim thay van da luu"}, 404)
        with open(duong, encoding="utf-8") as f:
            return self._tra(json.load(f))

    def _xoa_van(self, req):
        ma = req.get("ma", "")
        if not _MA_VAN_HOP_LE.match(ma):
            return self._tra({"loi": "ma khong hop le"}, 400)
        try:
            os.remove(os.path.join(THU_MUC_VAN, f"{ma}.json"))
        except FileNotFoundError:
            pass
        return self._tra({"ok": True})

    # -- bai tap chien thuat (xem tools/mine_puzzles.py) ---------------------

    def _giai_the_moi(self, req):
        if not _puzzles:
            return self._tra({"loi": "chua co bai tap nao - chay tools/mine_puzzles.py"}, 404)
        giai_doan = req.get("giai_doan")
        do_kho = req.get("do_kho")                     # 'de' | 'vua' | 'kho' | None
        # Nguong chon tu chinh phan bo xoay_chuyen thuc te cua 11.116 bai
        # (percentile 33/66) - khong phai so tuy tien.
        def kho_cua(x):
            if x < 90: return "de"
            return "vua" if x < 140 else "kho"
        # Bo qua cac bai da gap (client tu gui len danh sach FEN da giai) -
        # chi ap dung neu con du bai sau khi loc, khong thi bo qua rang buoc
        # nay de khong bao gio "het bai" khi nguoi choi da giai het mot muc.
        bo_qua = set(req.get("bo_qua") or [])
        con_lai = [p for p in _puzzles
                  if (not giai_doan or p.get("giai_doan") == giai_doan)
                  and (not do_kho or kho_cua(p["xoay_chuyen"]) == do_kho)]
        if not con_lai:
            con_lai = _puzzles
        chua_gap = [p for p in con_lai if p["fen"] not in bo_qua]
        if chua_gap:
            con_lai = chua_gap
        p = random.choice(con_lai)
        # nuoc_dung luu dang ICCS ("a0b0") trong file - doi san sang mang
        # [r0,c0,r1,c1] de client so sanh truc tiep, khong phai tu phan tich.
        try:
            nuoc_dung = list(iccs_to_move(p["nuoc_dung"]))
        except Exception:
            return self._tra({"loi": "bai tap hong"}, 500)
        return self._tra({
            "id": p["fen"], "fen": p["fen"],
            "ben_di": "trang" if p["ben_di"] == "w" else "den",
            "nuoc_dung": nuoc_dung,
            "diem_truoc": p["diem_truoc"], "diem_sau": p["diem_sau"],
            "xoay_chuyen": p["xoay_chuyen"], "giai_doan": p["giai_doan"],
            "do_kho": kho_cua(p["xoay_chuyen"]),
        })

    # -- duyet khai cuoc that tu chessdb (xem _nap_khai_cuoc o tren) --------

    def _duyet_khai_cuoc(self, req):
        if not _khai_cuoc:
            return self._tra({"loi": "chua co du lieu khai cuoc - can data/seeds_chessdb.jsonl"}, 404)
        so_luong = min(50, max(1, int(req.get("so_luong", 20))))
        mau = random.sample(_khai_cuoc, min(so_luong, len(_khai_cuoc)))
        return self._tra({"ds": mau, "tong": len(_khai_cuoc)})


def main():
    print("XuanWu - dang khoi dong...")
    ok = strongest.chuan_bi()
    print(f"  engine: {'loi C' if ok else 'Python thuan (cham hon)'}")
    from engine import book
    print(f"  sach khai cuoc: {book.so_muc():,} the co" if book.nap()
          else "  sach khai cuoc: khong co")
    _nap_puzzles()
    print(f"  bai tap chien thuat: {len(_puzzles):,} the" if _puzzles
          else "  bai tap chien thuat: khong co (chay tools/mine_puzzles.py)")
    _nap_khai_cuoc()
    print(f"  kho khai cuoc: {len(_khai_cuoc):,} the" if _khai_cuoc
          else "  kho khai cuoc: khong co (can data/seeds_chessdb.jsonl)")
    socketserver.TCPServer.allow_reuse_address = True
    # Lang nghe tren moi dia chi de may khac trong cung mang WiFi vao duoc.
    # Chi trong mang noi bo, khong ra Internet - an toan cho may ca nhan.
    with socketserver.ThreadingTCPServer(("0.0.0.0", CONG), May) as may:
        dia_chi = f"http://127.0.0.1:{CONG}/"
        dc = _dia_chi_lan(CONG)
        print(f"\n  May nay      : {dia_chi}")
        if dc:
            print(f"  May khac     : {dc['ip']}   (cung mang WiFi)")
            if dc.get("local"):
                print(f"  Ten de nho   : {dc['local']}   (thiet bi Apple khac, "
                      f"khong doi du IP bi cap lai)")
        print("  Nhan Ctrl+C de tat\n")
        # Chi tu mo trinh duyet khi chay tren MAY CA NHAN. Tren may chu that
        # (Render va tuong tu) luon co bien PORT, khong co man hinh GUI de mo
        # trinh duyet - goi webbrowser.open() o do vo nghia va co the loi.
        if "PORT" not in os.environ:
            threading.Timer(0.8, lambda: webbrowser.open(dia_chi)).start()
        try:
            may.serve_forever()
        except KeyboardInterrupt:
            print("\nDa tat.")


if __name__ == "__main__":
    main()
