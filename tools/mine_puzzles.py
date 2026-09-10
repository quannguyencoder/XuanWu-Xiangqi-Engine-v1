"""
QiSheng - khai thac "bai tap chien thuat" tu du lieu huan luyen da co san.

Vi sao lam duoc MA KHONG TON CHI PHI GI: du lieu tu tools/label_pikafish.py
la CAC NUOC LIEN TIEP CUA MOT VAN (Pikafish tu choi voi chinh no), khong phai
cac the co roi rac. Nghia la dong i va dong i+1 trong CUNG MOT file worker
thuong la hai the co lien tiep: dong i+1 chinh la ket qua cua viec choi
best_move cua dong i. Tu do suy ra duoc CHENH LECH DIEM ma nuoc do mang lai -
khong can chay lai engine mot lan nao.

"But phap chien thuat" = the co ma nuoc dung (best_move) mang lai mot buoc
NHAY DIEM lon cho ben di. Cang nhay nhieu, nuoc do cang "sac" - dung tim
cac buoc nhay do, khong phai chay MultiPV nhu cach lam thong thuong (cham
hon nhieu vi phai tim ca nuoc tot NHI cho tung the co).

Kiem chung tinh lien tuc: with tung cap dong, tu FEN + best_move cua dong i,
choi thu nuoc do bang chinh engine/board.py roi so voi FEN cua dong i+1 -
KHONG tin twong mu quang rang du lieu lien tuc, vi nhieu van khac nhau bi noi
duoi nhau trong cung mot file (moi lan choi xong mot van thi bat dau van moi).

Chay:
    python3 tools/mine_puzzles.py --nguong 70 --ra data/puzzles.jsonl
"""

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.board import make_move
from tools.collect_openings import board_to_fen, fen_to_board, iccs_to_move


def giai_doan_cua(fen: str) -> str:
    n = sum(1 for ch in fen.split()[0] if ch.isalpha())
    if n >= 28:
        return "khai"
    return "trung" if n >= 16 else "tan"


def khai_thac_mot_file(duong: str, nguong: float, da_thay: set):
    """Sinh cac ung vien puzzle tu MOT file, tra ve list dict."""
    ung_vien = []
    prev = None
    tong, lien_tuc = 0, 0
    with open(duong, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            tong += 1
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                prev = None
                continue
            if prev is not None:
                khop = False
                try:
                    b, _ = fen_to_board(prev["fen"])
                    mv = iccs_to_move(prev["best_move"])
                    sau = make_move(b, mv)
                    fen_du_doan = board_to_fen(sau, "b" if prev["side"] == "w" else "w")
                    khop = fen_du_doan.split()[0] == d["fen"].split()[0]
                except Exception:
                    khop = False
                if khop:
                    lien_tuc += 1
                    # Bo qua the co da qua thien lech - nguoi giai khong can
                    # tim gi tinh te vi ben do da thang/thua gan chac chan roi.
                    if 50 < prev["score"] < 950:
                        swing = d["score"] - prev["score"]
                        xoay = swing if prev["side"] == "w" else -swing
                        if xoay >= nguong and prev["fen"] not in da_thay:
                            da_thay.add(prev["fen"])
                            ung_vien.append({
                                "fen": prev["fen"],
                                "ben_di": prev["side"],
                                "nuoc_dung": prev["best_move"],
                                "diem_truoc": prev["score"],
                                "diem_sau": d["score"],
                                "xoay_chuyen": xoay,
                                "giai_doan": giai_doan_cua(prev["fen"]),
                            })
            prev = d
    return ung_vien, tong, lien_tuc


def main() -> None:
    ap = argparse.ArgumentParser(description="Khai thac bai tap chien thuat tu du lieu huan luyen")
    ap.add_argument("--vao", nargs="+", default=None,
                    help="Cac file jsonl nguon (mac dinh: data/data_pikafish_s*.jsonl)")
    ap.add_argument("--nguong", type=float, default=70.0,
                    help="Diem nhay toi thieu (thang 0-1000) de tinh la but phap")
    ap.add_argument("--ra", default="data/puzzles.jsonl")
    args = ap.parse_args()

    files = args.vao or sorted(glob.glob("data/data_pikafish_s*.jsonl"))
    print(f"Quet {len(files)} file, nguong xoay chuyen >= {args.nguong}", flush=True)

    t0 = time.time()
    tat_ca, da_thay = [], set()
    tong_dong = tong_lien_tuc = 0
    for duong in files:
        uv, tong, lien_tuc = khai_thac_mot_file(duong, args.nguong, da_thay)
        tat_ca.extend(uv)
        tong_dong += tong
        tong_lien_tuc += lien_tuc
        print(f"  {duong}: {tong:,} dong, {lien_tuc:,} lien tuc, "
              f"+{len(uv):,} ung vien (cong don {len(tat_ca):,})", flush=True)

    tat_ca.sort(key=lambda x: -x["xoay_chuyen"])
    os.makedirs(os.path.dirname(args.ra) or ".", exist_ok=True)
    with open(args.ra, "w", encoding="utf-8") as f:
        for u in tat_ca:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")

    dem_giai_doan = {"khai": 0, "trung": 0, "tan": 0}
    for u in tat_ca:
        dem_giai_doan[u["giai_doan"]] += 1

    print(f"\nXong trong {time.time()-t0:.0f}s")
    print(f"Tong {tong_dong:,} dong, {tong_lien_tuc:,} cap lien tuc "
          f"({tong_lien_tuc/max(1,tong_dong)*100:.0f}%)")
    print(f"Da ghi {len(tat_ca):,} bai tap vao {args.ra}")
    print(f"  khai cuoc: {dem_giai_doan['khai']:,}")
    print(f"  trung cuoc: {dem_giai_doan['trung']:,}")
    print(f"  tan cuoc: {dem_giai_doan['tan']:,}")


if __name__ == "__main__":
    main()
